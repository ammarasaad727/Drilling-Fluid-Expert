import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import numpy as np
import datetime

# ==========================================
# 1. إعدادات الصفحة و CSS
# ==========================================
st.set_page_config(page_title="Pro-Drill Fluid Consultant", page_icon="🛢️", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; border-left: 5px solid #0056b3; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
    .justification-box { background-color: #e9ecef; padding: 20px; border-radius: 10px; border-right: 5px solid #28a745; text-align: right; font-family: 'Arial';}
    .risk-high { background-color: #ffcccc; padding: 15px; border-radius: 8px; border-right: 5px solid #cc0000; margin-bottom: 10px;}
    .risk-med { background-color: #fff0b3; padding: 15px; border-radius: 8px; border-right: 5px solid #e6b800; margin-bottom: 10px;}
    </style>
    """, unsafe_allow_html=True)

# تهيئة القيم الافتراضية لمنع ظهور الـ KeyError
if 'analyzed' not in st.session_state:
    st.session_state['analyzed'] = False
if 'cost' not in st.session_state:
    st.session_state['cost'] = 0
if 'offset_data' not in st.session_state:
    st.session_state['offset_data'] = None

# ==========================================
# 2. الهوية البصرية (Sidebar)
# ==========================================
with st.sidebar:
    st.image("https://media.licdn.com/dms/image/v2/D5603AQGFR4yZ0Xbu8g/profile-displayphoto-scale_400_400/B56Zxrp.xPKgAg-/0/1771332696780?e=1776902400&v=beta&t=C61L4iXPusv4186Q35I2zsNKOkxJ8f6_21WQMbJjJbE", width="stretch")
    
    my_photo_url = "https://media.licdn.com/dms/image/v2/D4D03AQH_gUWhtKDArA/profile-displayphoto-scale_400_400/B4DZxtywF.HwAg-/0/1771368594755?e=1776902400&v=beta&t=gspy51NhC3MQcYR3OFvLXScf3zuY_Xmm4pM_5exbxGE"
    col1, col2 = st.sidebar.columns([1, 3])
    with col1:
        st.image(my_photo_url, width=60)
    with col2:
        st.write("### المهندس عمار أسعد")
        st.write("مطور التطبيق")
        
    st.sidebar.markdown("---")
    
    # ------------------------------------------
    # الإضافة الجديدة: رفع سجلات الآبار
    # ------------------------------------------
    st.sidebar.title("🗂️ بيانات الآبار المجاورة")
    uploaded_file = st.sidebar.file_uploader("ارفع سجلات الآبار (CSV)", type="csv")
    if uploaded_file is not None:
        try:
            st.session_state['offset_data'] = pd.read_csv(uploaded_file)
            st.sidebar.success("تم تحميل البيانات بنجاح!")
        except Exception as e:
            st.sidebar.error("خطأ في قراءة الملف. تأكد من أنه بصيغة CSV.")
    # ------------------------------------------

    st.sidebar.markdown("---")
    st.sidebar.title("📩 تواصل معي")
    linkedin_url = "https://www.linkedin.com/in/ammar-asaad/"
    st.sidebar.markdown(f'[![LinkedIn](https://img.shields.io/badge/LinkedIn-Profile-blue?style=for-the-badge&logo=linkedin)]({linkedin_url})')
    email = "ammarasaad727@gmail.com"
    st.sidebar.write(f"📧: {email}")
    st.markdown("---")
    st.write("⚙️ **الإصدار:** v5.1 Enterprise")
    
# ==========================================
# 3. الواجهة الرئيسية ومدخلات البيانات
# ==========================================
st.title("🛢️ Advanced Drilling Fluid Expert & Simulator")
st.markdown("نظام ذكي متكامل لمحاكاة وتصميم سوائل الحفر بناءً على المعطيات الجيولوجية والديناميكية.")

tabs = st.tabs(["📋 إعدادات البئر", "🧬 التوصيات الهندسية", "📈 نافذة الضغوط", "📦 اللوجستيات والاقتصاد", "⚠️ المخاطر والتاريخ", "📄 تصدير التقرير"])

with tabs[0]:
    st.markdown("### 🌍 المعطيات الأساسية للحقل")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        formation = st.selectbox("نوع التكوين الجيولوجي:", ["Sandstone (Clean)", "Reactive Shale", "Evaporites (Salt)", "High-Perm Carbonate"])
        hole_size = st.number_input("قطر البئر (in):", value=12.25, step=0.125)
    with col2:
        depth = st.number_input("العمق الإجمالي (m):", value=3000, step=100)
        temp = st.slider("درجة الحرارة المتوقعة (°C):", 20, 200, 110)
    with col3:
        pore_press = st.number_input("ضغط المسام (Pore Press - ppg):", value=9.5, step=0.1)
        frac_press = st.number_input("ضغط الكسر (Frac Press - ppg):", value=13.5, step=0.1)
        safety_margin = st.slider("هامش الأمان (Trip Margin - ppg):", 0.1, 1.0, 0.5)
    with col4:
        pump_rate = st.number_input("معدل الضخ (GPM):", value=600, step=50)
        has_h2s = st.checkbox("وجود غاز H2S محتمل؟")

target_mw = pore_press + safety_margin
ecd_estimate = target_mw + (pump_rate * 0.0005) 

with tabs[1]:
    if st.button("🚀 تشغيل محاكاة النظام الهندسي", width="stretch", type="primary"):
        st.session_state['analyzed'] = True
        if formation == "Reactive Shale" or temp > 150:
            system, cost = "High-Performance Invert Emulsion (OBM/SBM)", 120
            why = "اختيار OBM لحماية الـ Shale واستقرار البئر في الحرارة العالية."
            pv, yp, fluid_loss = "15 - 25", "10 - 15", "< 4.0 cc"
        elif formation == "Evaporites (Salt)":
            system, cost = "Saturated Brine WBM", 85
            why = "استخدام ملوحة مشبعة لمنع ذوبان الملح (Salt Creep)."
            pv, yp, fluid_loss = "12 - 20", "15 - 20", "< 6.0 cc"
        else:
            system, cost = "Polymer Water-Based Mud (WBM)", 45
            why = "نظام اقتصادي وصديق للبيئة للطبقات المستقرة."
            pv, yp, fluid_loss = "10 - 18", "12 - 18", "< 5.0 cc"
        st.session_state.update({'system': system, 'why': why, 'pv': pv, 'yp': yp, 'fl': fluid_loss, 'cost': cost})

    if st.session_state['analyzed']:
        st.success(f"**النظام الموصى به:** {st.session_state['system']}")
        st.markdown(f"<div class='justification-box'>{st.session_state['why']}</div>", unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("كثافة الطفلة (MW)", f"{target_mw:.2f} ppg")
        c2.metric("اللزوجة البلاستيكية (PV)", st.session_state['pv'], "cP")
        c3.metric("نقطة الخضوع (YP)", st.session_state['yp'], "lb/100ft²")
        c4.metric("الكثافة الدورانية (ECD)", f"{ecd_estimate:.2f} ppg")

with tabs[2]:
    if st.session_state['analyzed']:
        depths = np.linspace(0, depth, 50)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=np.linspace(8.5, pore_press, 50), y=depths, mode='lines', name='Pore Pressure', line=dict(color='red')))
        fig.add_trace(go.Scatter(x=np.full_like(depths, target_mw), y=depths, mode='lines', name='MW', line=dict(color='blue', dash='dash')))
        fig.add_trace(go.Scatter(x=np.full_like(depths, ecd_estimate), y=depths, mode='lines', name='ECD', line=dict(color='orange')))
        fig.add_trace(go.Scatter(x=np.linspace(11.0, frac_press, 50), y=depths, mode='lines', name='Frac Pressure', line=dict(color='green')))
        fig.update_layout(yaxis=dict(autorange="reversed"), height=500)
        st.plotly_chart(fig, use_container_width=True)

with tabs[3]:
    if st.session_state['analyzed']:
        volume_bbl = round(((hole_size**2) / 1029.4) * (depth * 3.281) * 1.5)
        barite = round((1470 * (target_mw - 8.33) / (35 - target_mw)) * (volume_bbl / 100))
        total_cost = volume_bbl * st.session_state['cost']
        c1, c2, c3 = st.columns(3)
        c1.metric("حجم النظام", f"{volume_bbl} bbl")
        c2.metric("أكياس البارايت", f"{barite} Sack")
        c3.metric("التكلفة", f"${total_cost:,.2f}")

with tabs[4]:
    if st.session_state['analyzed']:
        if has_h2s: st.error("🚨 خطر H2S: أضف Zinc Carbonate فوراً!")
        
        # ------------------------------------------
        # التعديل الجديد لقراءة بيانات الملف المرفوع
        # ------------------------------------------
        st.markdown("### 📊 بيانات الآبار المجاورة (Offset Wells)")
        
        if st.session_state['offset_data'] is not None:
            df = st.session_state['offset_data']
            st.dataframe(df, use_container_width=True)
            
            # نفترض أن ملف الـ CSV يحتوي على عمود باسم Max_MW للمقارنة
            if 'Max_MW' in df.columns:
                max_mw = df['Max_MW'].max()
                if target_mw > max_mw:
                    st.markdown(f"<div class='risk-high'>🚨 <strong>تحذير هندسي:</strong> الكثافة المقترحة ({target_mw:.2f} ppg) أعلى من أقصى كثافة مستخدمة سابقاً ({max_mw} ppg). خطر فقدان الدورة!</div>", unsafe_allow_html=True)
                else:
                    st.success(f"✅ الكثافة المقترحة ({target_mw:.2f} ppg) تعتبر آمنة بناءً على تاريخ الآبار المجاورة.")
            else:
                st.info("💡 تم تحميل البيانات بنجاح، لكن يرجى التأكد من وجود عمود باسم 'Max_MW' لإجراء المقارنة التلقائية للكثافة.")
        else:
            st.warning("⚠️ لم يتم رفع بيانات آبار مجاورة. يرجى رفع ملف CSV من القائمة الجانبية لإجراء المقارنة وتقييم المخاطر.")
        # ------------------------------------------

with tabs[5]:
    if st.session_state['analyzed']:
        report = f"Report Date: {datetime.datetime.now()}\nEngineer: Ammar Asaad\nSystem: {st.session_state['system']}\nCost: ${total_cost:,.2f}"
        st.text_area("تقرير ملخص", report, height=150)
        st.download_button("تحميل التقرير", report)

st.markdown("---")
st.caption("Developed by Ammar Asaad | The Digital Petroleum Engineer | © 2026")
