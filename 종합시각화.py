#1. 필요라이브러 가져오기
import streamlit as st
import folium
from streamlit_folium import st_folium
import pandas as pd
import numpy as np
from folium import Icon
#===========================================================
#===========================================================


st.set_page_config(
    page_title="대구시 공영주차장 통합 대시보드",
    layout="wide",  # 👈 전체 폭 사용
    initial_sidebar_state="collapsed"
)

#2. 데이터 경로 설정
MAIN_DATA_PATH = '태양광_일사량 및 주차 구획수.xlsx' 
CONGESTION_DATA_PATH = '혼잡도_요일별_시간별_요약.xlsx'

#===========================================================
#===========================================================

#3. 고정 파라미터 설정 (초기값으로 사용됨)
DEFAULT_EV_COUNT_PER_DAY = 4 
#EV 평균 배터리 용량 
EV_BATTERY_KWH = 80 
#태양광으로 충당할 전체 충전량 비율 
DEFAULT_PV_TARGET_RATIO = 0.30 
#ESS round-trip efficiency 
ESS_RTE = 0.85 
#태양광 모듈 효율 
PV_EFFICIENCY = 0.18 
#인버터 및 시스템 손실 반영 
SYSTEM_LOSS = 0.80 
#한 주차구획 면적 (m²) 
PARKING_AREA_PER_SLOT = 12.5 
# 1년 
DAYS_PER_YEAR = 365

#===========================================================
#===========================================================

# 4.태양광 일사량 적합도 분류 (사용자 파라미터 받도록 수정)
# @st.cache_data를 사용하여 동일 파라미터 시 재계산 방지 (성능 최적화)
@st.cache_data(show_spinner="태양광 적합도 재계산 중...")
def calculate_pv_requirements(file_path, ev_count_per_day, pv_target_ratio):
    # 데이터 불러오기
    df = pd.read_excel(file_path)
    
    #하루 목표 태양광 발전량 (ESS 효율 반영)
    daily_ev_demand = ev_count_per_day * EV_BATTERY_KWH
    target_pv_energy = daily_ev_demand * pv_target_ratio
    required_pv_output = target_pv_energy / ESS_RTE  # kWh/day
    
    #주차장별 계산 수행
    df["㎡당_일평균_발전량(kWh/m²/day)"] = (
        df["㎡당 연간 일사량(kWh/m²/yr)"] * PV_EFFICIENCY * SYSTEM_LOSS / DAYS_PER_YEAR
    )
    
    # required_pv_output이 0일 경우 (즉, pv_target_ratio가 0일 경우) '필요패널면적'을 0으로 설정
    if required_pv_output == 0:
        df["필요패널면적(m²)"] = 0
    else:
        df["필요패널면적(m²)"] = required_pv_output / df["㎡당_일평균_발전량(kWh/m²/day)"]

    df["필요구획수"] = df["필요패널면적(m²)"] / PARKING_AREA_PER_SLOT

    #적합/부적합 기준 분류
    df["태양광 적합 여부"] = df.apply(
        lambda row: (
            # 기존 조건: 필요구획수 < 80 이면서 총주차면수의 50%를 넘을 경우 "부적합"
            # 조건이 복잡하여, 필요구획수가 총 주차면수의 50%보다 클 경우 '부적합'으로 단순화 해석
            # (필요구획수 < 80 and row["필요구획수"] > row["총주차면수"] * 0.5)
            
            # 필요구획수가 총 주차면수의 50%를 초과할 경우
            "부적합" if row["필요구획수"] > row["총주차면수"] * 0.5
            else "적합"
        ),
        axis=1
    )
    
    #정리
    result = df[
        [
            "주차장_ID", "지번주소", "주차장명", "총주차면수",
            "㎡당 연간 일사량(kWh/m²/yr)",
            "필요패널면적(m²)", "필요구획수", "태양광 적합 여부",
            "위도", "경도"
        ]
    ]
    
    return result.round(2)

#===========================================================
#===========================================================

#5. 혼잡도 상태 분류 (파일 경로만 받도록 수정)
@st.cache_data
def classify_congestion(congestion_file_path):
    #혼잡도 엑셀 파일의 모든 시트 읽기 (월~일)
    sheets = pd.read_excel(congestion_file_path, sheet_name=None, index_col=0)

    #모든 요일 시트의 합계를 계산
    total_congestion = None
    for day, df_day in sheets.items():
        # % 기호 제거 및 float 변환
        df_day = df_day.replace('%', '', regex=True).astype(float)
        
        if total_congestion is None:
            total_congestion = df_day
        else:
            total_congestion += df_day
    
    #주차장별 일주일 총합 평균 (시간별 평균을 통해)
    weekly_avg_congestion = total_congestion.mean(axis=0)  # axis=0 → 주차장별 평균
    
    #0~1 정규화
    min_val, max_val = weekly_avg_congestion.min(), weekly_avg_congestion.max()
    normalized = (weekly_avg_congestion - min_val) / (max_val - min_val)
    
    #혼잡도 라벨링
    def congestion_label(x):
        if pd.isna(x):
            return np.nan
        elif x < 0.6:
            return '여유'
        elif x < 0.9:
            return '보통'
        else:
            return '혼잡'
    
    congestion_labels = normalized.apply(congestion_label)
    
    #DataFrame으로 변환
    congestion_df = pd.DataFrame({
        '주차장_ID': normalized.index,
        '정규화_혼잡도': normalized.values,
        '혼잡도': congestion_labels.values
    })
    
    return congestion_df

# 6. 메인 데이터프레임 생성 및 캐싱
# 혼잡도 데이터는 고정되어 있으므로 한번만 계산
@st.cache_data
def create_initial_df(main_path, congestion_path):
    # 5. 혼잡도 분류 결과
    congestion_df = classify_congestion(congestion_path)
    
    # 4. 태양광 초기 계산 (초기값 사용)
    pv_df_initial = calculate_pv_requirements(main_path, DEFAULT_EV_COUNT_PER_DAY, DEFAULT_PV_TARGET_RATIO)
    
    # 초기 merge: 이 시점에서는 태양광 적합 여부와 관계없이 일단 merge
    initial_merged = pv_df_initial.merge(congestion_df, on='주차장_ID', how='left', suffixes=('_pv', '_cg'))

    columns_to_keep = [
        '주차장_ID', '주차장명', '지번주소', '총주차면수',
        '㎡당 연간 일사량(kWh/m²/yr)', 
        '위도', '경도', # 고정 값들
        '정규화_혼잡도', '혼잡도' # 혼잡도 데이터
    ]
    
    # 태양광/필요구획수 관련 컬럼은 제거하고 고정 데이터만 남김
    initial_df = initial_merged[columns_to_keep]
    initial_df.reset_index(drop=True, inplace=True)
    return initial_df

# 고정 데이터 로드
base_df = create_initial_df(MAIN_DATA_PATH, CONGESTION_DATA_PATH)

#===========================================================
#===========================================================
#####시각화
#===========================================================
#===========================================================

#1. 세션 초기화

if 'selected_parking' not in st.session_state:
    st.session_state.selected_parking = None

#===========================================================
# 7. 사용자 설정 영역 추가
st.markdown("## ☀️⚡ 대구시 공영주차장 태양광 적합 및 혼잡도 대시보드")

config_col1, config_col2 = st.columns(2)

with config_col1:
    st.markdown("#### 🚗 일일 평균 EV 충전 대수")
    user_ev_count = st.slider(
        "하루 EV 충전 대수", 
        min_value=1, 
        max_value=10, 
        value=DEFAULT_EV_COUNT_PER_DAY, 
        step=1, 
        key="ev_count_slider"
    )

with config_col2:
    st.markdown("#### 🌞 EV 충전량 중 태양광 충당 목표 비율 (%)")
    # 슬라이더는 0.1부터 시작하여 10% 단위로 설정 (0.1, 0.2, ...)
    user_pv_ratio = st.slider(
        "태양광 충당 목표 비율", 
        min_value=10, 
        max_value=40, 
        value=int(DEFAULT_PV_TARGET_RATIO * 100), 
        step=10, 
        format="%d%%",
        key="pv_ratio_slider"
    ) / 100.0 # 비율로 사용하기 위해 100으로 나눔

# 사용자 설정 값을 사용하여 최종 데이터프레임 재계산
# 이 과정이 calculate_pv_requirements의 @st.cache_data를 트리거함
pv_recalculated_df = calculate_pv_requirements(MAIN_DATA_PATH, user_ev_count, user_pv_ratio)

# 최종 데이터프레임 병합: 고정 데이터(base_df)에 재계산된 태양광 정보 병합
# merge 시 주차장_ID, 위도, 경도 컬럼은 pv_recalculated_df에서 가져옴
# 혼잡도 관련 컬럼은 base_df에서 가져옴
final_df = base_df.drop(columns=['㎡당 연간 일사량(kWh/m²/yr)']).merge(
    pv_recalculated_df[['주차장_ID', '필요패널면적(m²)', '필요구획수', '태양광 적합 여부', '㎡당 연간 일사량(kWh/m²/yr)']], 
    on='주차장_ID', 
    how='left'
)

# 7️⃣ 태양광 부적합 주차장은 혼잡도 NaN 처리 (classify_congestion에서 분리)
final_df.loc[final_df['태양광 적합 여부'] == '부적합', ['정규화_혼잡도', '혼잡도']] = np.nan

# 컬럼 순서 정리
columns_to_display = [
    '주차장_ID', '주차장명', '지번주소', '총주차면수',
    '㎡당 연간 일사량(kWh/m²/yr)', '필요패널면적(m²)', '필요구획수',
    '태양광 적합 여부', '정규화_혼잡도', '혼잡도', '위도', '경도'
]
final_df = final_df[columns_to_display]
final_df.reset_index(drop=True, inplace=True)

#===========================================================

st.markdown(""""
    <style>
    [data-testid="stHorizontalBlock"] {
        gap: 0.5rem !important;  /* 컬럼 간 간격 줄이기 */
    }
    </style>
""", unsafe_allow_html=True)

col1, col3, col2 = st.columns([4.5, 3.5, 2.5])

with col1:
    st.subheader("🗺️ 주차장 지도")
    
    st.markdown(
        "대구광역시에 위치한 공영주차장 중 지상 노외 주차장들의 위치입니다. "
        "마커를 클릭하면 해당 주차장의 혼잡도 그래프와 상세 정보를 확인할 수 있습니다."
    )
    
    # 지도 중심 계산
    map_center = [final_df["위도"].mean(), final_df["경도"].mean()]
    m = folium.Map(location=map_center, zoom_start=13)

    # 마커 색상 지정 함수
    def get_marker_color(row):
        # 태양광 부적합 → 검정
        if row["태양광 적합 여부"] == "부적합":
            return "black"
        # 혼잡도에 따른 색상 (태양광 적합일 경우에만 적용)
        elif row["혼잡도"] == "혼잡":
            return "red"
        elif row["혼잡도"] == "보통":
            return "orange"  # folium에 'yellow'가 잘 안 보이므로 orange가 가시성 좋음
        elif row["혼잡도"] == "여유":
            return "blue"
        else:
            # 태양광 적합인데 혼잡도 정보가 NaN인 경우 (여유 범주로 분류되나, 안전을 위해 회색)
            return "gray"

    # 마커 추가
    for idx, row in final_df.iterrows():
        color = get_marker_color(row)
        
        # HTML 팝업
        html = f"""
        <div style="
            font-family: Arial; 
            font-size: 14px; 
            line-height: 1.5; 
            background-color: white; 
            border: 2px solid {color};
            border-radius: 8px;
            padding: 8px;
            width: 220px;
        ">
            <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
                <span style="font-size:16px;">&#128664;</span> <b>주차장명:</b> {row['주차장명']}
            </div>
            <div><b>혼잡도:</b> {row['혼잡도'] if pd.notna(row['혼잡도']) else 'N/A'}</div>
            <div><b>태양광 적합 여부:</b> {row['태양광 적합 여부']}</div>
        </div>
        """
        
        iframe = folium.IFrame(html, width=300, height=110)
        popup = folium.Popup(iframe, max_width=300)

        folium.Marker(
            location=[row["위도"], row["경도"]],
            popup=popup,
            tooltip=row["주차장_ID"],
            icon=folium.Icon(color=color, icon="info-sign", prefix="glyphicon")
        ).add_to(m)

    # 지도 표시 및 클릭 이벤트
    map_data = st_folium(m, width=900, height=650)

    # 클릭 시 가장 가까운 주차장 탐색
    if map_data["last_clicked"]:
        lat, lon = map_data["last_clicked"]["lat"], map_data["last_clicked"]["lng"]

        temp_df = final_df.copy()
        temp_df["거리"] = ((temp_df["위도"] - lat)**2 + (temp_df["경도"] - lon)**2)**0.5
        nearest = temp_df.loc[temp_df["거리"].idxmin()]

        st.session_state.selected_parking = nearest["주차장_ID"]

    # =========================
    # 지도 범례 박스
    # =========================
    st.markdown(
    """
    <div style="
        background-color: white;
        border-radius: 10px;
        padding: 10px;
        width: auto;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.15);
        display: flex;
        justify-content: space-around;
        align-items: center;
        margin-top: 10px;
    ">
        <div style="color:black; text-align:center;">⬤<br>태양광 부적합</div>
        <div style="color:red; text-align:center;">⬤<br>혼잡</div>
        <div style="color:orange; text-align:center;">⬤<br>보통</div>
        <div style="color:blue; text-align:center;">⬤<br>여유</div>
        <div style="color:gray; text-align:center;">⬤<br>정보 없음</div>
    </div>
    """, unsafe_allow_html=True
    )

# -------------------------------------------------------
# -------------------------------------------------------

# 3. 📊 상세 정보 구역
with col2:
    
    st.subheader("📊 선택 주차장 상세 정보")
    
    # 선택 초기화 버튼
    if st.button("선택 초기화 🔄"):
        st.session_state.selected_parking = None
        st.rerun()

    # 선택된 주차장 정보 표시
    if st.session_state.selected_parking:
        # 안전 체크: 해당 ID가 실제로 존재하는지 확인
        matched_rows = final_df[final_df["주차장_ID"] == st.session_state.selected_parking]

        if not matched_rows.empty:
            info = matched_rows.iloc[0]

            st.markdown(f"**🏷️ 주차장명:** {info['주차장명']}")
            st.markdown(f"**🆔 주차장 ID:** {info['주차장_ID']}")
            st.markdown(f"**📍 주소:** {info['지번주소']}")
            st.markdown(f"**🚗 총 주차면수:** {info['총주차면수']}")
            
            st.markdown("---")
            st.markdown(f"**☀️㎡당 연간 일사량:** {info['㎡당 연간 일사량(kWh/m²/yr)']} kWh/m²/yr")
            st.markdown(f"**🔋 필요패널면적:** {info['필요패널면적(m²)']} m²")
            st.markdown(f"**🧩 필요구획수:** {info['필요구획수']}")
            st.markdown(f"**🌞 태양광 적합 여부:** {info['태양광 적합 여부']}")

            st.markdown("---")
            st.markdown("**📈 혼잡도 상태:**")

            if pd.notna(info["정규화_혼잡도"]):
                st.progress(int(info["정규화_혼잡도"] * 100))
                st.markdown(f"**혼잡도 등급:** {info['혼잡도']} ({int(info['정규화_혼잡도'] * 100)}%)")
            else:
                st.warning("혼잡도 표시 불가 (태양광 부적합이거나 데이터 없음)")
        else:
            st.error("❌ 선택한 주차장 ID에 해당하는 데이터가 없습니다.")
            st.session_state.selected_parking = None

    else:
        st.info("지도의 주차장을 클릭하면 상세 정보를 볼 수 있습니다.")

    st.markdown("</div>", unsafe_allow_html=True)  # 박스 종료
# -------------------------------------------------------
# -------------------------------------------------------

#4. 혼잡도 그래프
with col3:
    # st.markdown(
    #     """
    #     <div style="
    #         background-color: #d6f0ff;  /* 연한 하늘색 */
    #         border-radius: 15px; 
    #         padding: 15px;
    #         box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
    #     ">
    #     """, unsafe_allow_html=True
    # )

    st.subheader("🕒 요일별 시간대 혼잡도 추이")

    # 혼잡도 데이터 불러오기
    @st.cache_data
    def load_congestion_data():
        # 데이터 파일이 이미 '혼잡도_요일별_시간별_요약.xlsx'로 고정되어 있음
        sheets = pd.read_excel(CONGESTION_DATA_PATH, sheet_name=None, index_col=0)
        return sheets

    congestion_sheets = load_congestion_data()
    days = list(congestion_sheets.keys())

    # 주차장 선택 확인
    if st.session_state.selected_parking:
        selected_id = st.session_state.selected_parking

        # 요일 선택 박스
        selected_day = st.selectbox("📅 요일 선택", days, index=0, key="day_selector")

        # 선택한 요일의 시트 가져오기
        congestion_df = congestion_sheets[selected_day]

        # 시간 컬럼 이름이 자동으로 첫 번째일 가능성 처리
        time_col = congestion_df.columns.name if congestion_df.columns.name else congestion_df.index.name
        
        # 선택한 주차장이 해당 시트에 존재할 경우
        if selected_id in congestion_df.columns:
            # 혼잡도 데이터 (인덱스가 시간, 컬럼이 주차장 ID)
            df_plot = congestion_df[[selected_id]].copy()
            df_plot.index.name = "시간"
            df_plot.rename(columns={selected_id: "혼잡도"}, inplace=True)
            df_plot.reset_index(inplace=True)
            
            # % 제거 및 float 변환
            df_plot['혼잡도'] = df_plot['혼잡도'].astype(str).str.replace('%', '').astype(float)


            import plotly.express as px

            fig = px.line(
                df_plot,
                x="시간",
                y="혼잡도",
                markers=True,
                title=f"📊 {selected_day} - {selected_id} 주차장 혼잡도 변화",
            )
            fig.update_layout(
                xaxis_title="시간대 (시)",
                yaxis_title="혼잡도 (%)",
                template="plotly_white",
                height=400
            )
            st.plotly_chart(fig, use_container_width=True)

        else:
            st.warning("⚠️ 선택한 주차장은 이 요일의 데이터에 없습니다.")
    else:
        st.info("ℹ️ 지도의 마커를 클릭하면 주차장 혼잡도 추이를 볼 수 있습니다.")

    st.markdown("</div>", unsafe_allow_html=True)  # 박스 종료
