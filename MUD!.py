# app.py (نسخة مُنقّحة ونهائية - مدمج مع ميزات التصدير ورفع بيانات الآبار المجاورة)
import io
import re
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from fpdf import FPDF
from PIL import Image
from pint import UnitRegistry

# ---------------------------
# إعداد الصفحة و CSS
# ---------------------------
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

# ---------------------------
# وحدة القياس (pint)
# ---------------------------
ureg = UnitRegistry()
Q_ = ureg.Quantity
g = 9.80665  # m/s^2

# ---------------------------
# تهيئة آمنة لحالة الجلسة
# ---------------------------
st.session_state.setdefault('analyzed', False)
st.session_state.setdefault('offset_data', None)
st.session_state.setdefault('products', None)
st.session_state.setdefault('site_inventory', None)
st.session_state.setdefault('ecd_total', None)
st.session_state.setdefault('ecd_details', None)
st.session_state.setdefault('system', None)
st.session_state.setdefault('why', None)
st.session_state.setdefault('pv', None)
st.session_state.setdefault('yp', None)
st.session_state.setdefault('fl', None)

# ---------------------------
# دوال مساعدة
# ---------------------------
@st.cache_data
def get_sample_csv_bytes() -> bytes:
    df_sample = pd.DataFrame({
        "Well_Name": ["Well-A", "Well-B", "Well-C"],
        "Max_Depth_m": [3000, 3100, 2950],
        "Max_MW": [10.5, 12.0, 11.2],
        "Mud_Type": ["WBM", "OBM", "WBM"],
        "Issues": ["None", "Lost Circulation", "Tight Hole"]
    })
    return df_sample.to_csv(index=False).encode('utf-8')

def validate_offset_df(df: pd.DataFrame) -> Tuple[bool, str]:
    required = {"Well_Name", "Max_Depth_m", "Max_MW"}
    missing = required - set(df.columns)
    if missing:
        return False, f"الملف يفتقد الأعمدة التالية: {', '.join(missing)}"
    try:
        df['Max_MW'] = pd.to_numeric(df['Max_MW'])
        df['Max_Depth_m'] = pd.to_numeric(df['Max_Depth_m'])
    except Exception:
        return False, "أحد الأعمدة الرقمية يحتوي على قيم غير رقمية."
    return True, "OK"

def parse_unit_size_to_qty(unit_size: str) -> Tuple[Optional[float], Optional[str]]:
    """
    يحاول استخراج القيمة والوحدة من نص مثل '1 MT', '25 kg', '55 gal', '50 lb'
    يعيد (قيمة, وحدة_مبسطة) حيث الوحدة من {'kg','lb','gal'} أو (None,None) إذا لم تُعرف.
    """
    if unit_size is None:
        return None, None
    s = str(unit_size).strip().lower()
    # استخراج الرقم (يدعم الفواصل العشرية)
    m = re.search(r'([\d,\.]+)', s)
    if not m:
        return None, None
    raw_val = m.group(1).replace(',', '')
    try:
        val = float(raw_val)
    except Exception:
        return None, None

    # تطبيع الوحدة
    if re.search(r'\b(mt|ton|metric ton|tonne)\b', s):
        return val * 1000.0, 'kg'
    if 'kg' in s:
        return val, 'kg'
    if re.search(r'\b(lb|lbs|pound|pounds)\b', s):
        return val, 'lb'
    if re.search(r'\b(gal|gallon|gallons)\b', s):
        return val, 'gal'
    # لا تعرف الوحدة: إرجاع القيمة الخام (افتراضي kg)
    return val, None

def compute_volume(hole_size: float, depth: float, unit_system: str) -> Tuple[float, str]:
    """
    يحسب حجم السائل التقريبي مع هامش 50% (عامل 1.5)؛
    في API: hole_size بالإنش، depth بالقدم -> bbl
    في SI: hole_size بالسنتيمتر، depth بالمتر -> m^3
    """
    if unit_system == "API (Imperial)":
        # صيغة تقريبية لحجم البئر بالبرميل (bbl)
        volume_bbl = ((hole_size ** 2) / 1029.4) * depth * 1.5
        return round(volume_bbl, 2), "bbl"
    else:
        # hole_size بالسنتيمتر -> متر
        hole_m = hole_size / 100.0
        radius = hole_m / 2.0
        vol_m3 = np.pi * (radius ** 2) * depth * 1.5
        return round(vol_m3, 3), "m³"

def swamee_jain_f(Re: float, Dh: float, roughness: float = 4.5e-5) -> float:
    if Re <= 0 or Dh <= 0:
        return 0.02
    try:
        term = roughness/(3.7*Dh) + 5.74/(Re**0.9)
        f = 0.25 / (np.log10(term)**2)
        return float(f)
    except Exception:
        return 0.02

def compute_darcy_head_loss_annulus(pump_rate: float, hole_id: float, pipe_od: float, depth: float,
                                    unit_system: str, mud_density: float, mu_pa_s: float) -> Tuple[float, float]:
    """
    Returns (deltaP_Pa, delta_mw_ppg_estimate)
    - For API: hole_id and pipe_od in inches, pump_rate in GPM, depth in ft
    - For SI: hole_id and pipe_od in cm, pump_rate in L/min, depth in m
    """
    # تحويل الوحدات إلى SI داخل الحساب
    if unit_system == "API (Imperial)":
        hole_m = hole_id * 0.0254
        pipe_m = pipe_od * 0.0254
        L = depth * 0.3048
        Q_m3_s = (pump_rate * 0.00378541) / 60.0
    else:
        hole_m = hole_id / 100.0
        pipe_m = pipe_od / 100.0
        L = depth
        Q_m3_s = (pump_rate / 1000.0) / 60.0

    A_ann = np.pi * (((hole_m/2.0)**2) - ((pipe_m/2.0)**2))
    if A_ann <= 0 or hole_m <= pipe_m:
        return 0.0, 0.0

    V = Q_m3_s / A_ann
    Dh = hole_m - pipe_m
    rho = mud_density
    Re = (rho * V * Dh) / (mu_pa_s + 1e-12)
    f = swamee_jain_f(Re, Dh)
    deltaP = f * (L / Dh) * (0.5 * rho * V**2)
    delta_rho = deltaP / (g * L) if L > 0 else 0.0
    # تحويل delta_rho إلى ppg تقريباً
    delta_mw_ppg = delta_rho / 119.8264
    return float(deltaP), float(delta_mw_ppg)

def compute_ecd_with_darcy(target_mw: float, pump_rate: float, hole_id: float, pipe_od: float,
                           depth: float, unit_system: str, apparent_visc_cP: float, mud_type: str) -> Tuple[float, dict]:
    # تحويل MW إلى كثافة (rho)
    if unit_system == "API (Imperial)":
        rho = target_mw * 119.8264  # ppg -> kg/m3 approx factor used in original
    else:
        rho = target_mw * 1000.0
    mu_pa_s = (apparent_visc_cP / 1000.0)
    deltaP_pa, delta_mw_ppg = compute_darcy_head_loss_annulus(
        pump_rate=pump_rate,
        hole_id=hole_id,
        pipe_od=pipe_od,
        depth=depth,
        unit_system=unit_system,
        mud_density=rho,
        mu_pa_s=mu_pa_s
    )
    if unit_system == "API (Imperial)":
        ecd_total = target_mw + delta_mw_ppg
        delta_sg = delta_mw_ppg * 119.8264 / 1000.0
    else:
        delta_sg = delta_mw_ppg * 119.8264 / 1000.0
        ecd_total = target_mw + delta_sg

    details = {
        "deltaP_Pa": round(deltaP_pa, 2),
        "delta_MW_ppg": round(delta_mw_ppg, 4) if unit_system == "API (Imperial)" else None,
        "delta_SG": round(delta_sg, 6),
        "rho_kg_m3": round(rho, 2),
        "viscosity_Pa_s": round(mu_pa_s, 6)
    }
    return round(ecd_total, 4), details

def estimate_units_and_cost_precise(df_products: pd.DataFrame, volume: float, vol_unit: str) -> Tuple[float, pd.DataFrame]:
    total_cost = 0.0
    details = []
    for _, row in df_products.iterrows():
        conc = float(row.get("Conc (lb/bbl)", 0) or 0)
        unit_size = str(row.get("Unit Size", ""))
        cost_per_unit = float(row.get("Cost per Unit ($)", 0) or 0)
        qty_val, qty_unit = parse_unit_size_to_qty(unit_size)

        if vol_unit == "bbl":
            total_mass_lb = conc * volume
            if qty_unit == 'lb' and qty_val:
                units_needed = total_mass_lb / qty_val
            elif qty_unit == 'kg' and qty_val:
                total_mass_kg = total_mass_lb * 0.45359237
                units_needed = total_mass_kg / qty_val
            elif qty_unit == 'gal' and qty_val:
                assumed_lb_per_gal = 8.34
                units_needed = total_mass_lb / (assumed_lb_per_gal * qty_val)
            else:
                units_needed = total_mass_lb / 50.0
        else:
            conc_kg_per_m3 = conc * 0.45359237 / 0.158987
            total_mass_kg = conc_kg_per_m3 * volume
            if qty_unit == 'kg' and qty_val:
                units_needed = total_mass_kg / qty_val
            elif qty_unit == 'lb' and qty_val:
                units_needed = (total_mass_kg / 0.45359237) / qty_val
            elif qty_unit == 'gal' and qty_val:
                assumed_kg_per_gal = 3.78541
                units_needed = total_mass_kg / (assumed_kg_per_gal * qty_val)
            else:
                units_needed = total_mass_kg / 50.0

        est_cost = max(0, units_needed) * cost_per_unit
        total_cost += est_cost
        details.append({
            "Product": row.get("Product"),
            "Units Needed (est)": round(units_needed, 2),
            "Unit Size": unit_size,
            "Cost per Unit ($)": cost_per_unit,
            "Estimated Cost ($)": round(est_cost, 2)
        })
    details_df = pd.DataFrame(details)
    return round(total_cost, 2), details_df

# ---------------------------
# بدائل الأنظمة (mapping)
# ---------------------------
MUD_ALTERNATIVES = {
    "High-Performance Invert Emulsion (OBM/SBM)": [
        ("Synthetic OBM Blend", 0.9),
        ("SBM Light Formulation", 0.8),
        ("Polymer WBM with Oil Additives", 0.6),
        ("Saturated Brine WBM (as last resort)", 0.4)
    ],
    "Saturated Brine WBM": [
        ("Saturated Brine WBM (same)", 1.0),
        ("High-Salinity WBM", 0.85),
        ("Polymer WBM with KCl", 0.6)
    ],
    "Polymer Water-Based Mud (WBM)": [
        ("Polymer WBM (same)", 1.0),
        ("Low-Solids WBM", 0.85),
        ("Saturated Brine WBM (if salt present)", 0.5)
    ]
}

# ---------------------------
# دوال إنشاء ملفات للتنزيل (تصدير Excel و PDF)
# ---------------------------
def create_excel_report(ecd_total, ecd_details, details_df, logistics_df, meta):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        # ورقة الملخص
        summary_df = pd.DataFrame([{
            "Well": meta.get("Well", ""),
            "Unit System": meta.get("Unit System", ""),
            "Hole Size": meta.get("Hole Size", ""),
            "Depth": meta.get("Depth", ""),
            "Pump Rate": meta.get("Pump Rate", ""),
            "Target MW": meta.get("Target MW", ""),
            "ECD": ecd_total
        }])
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        # ورقة تفاصيل التكلفة
        if details_df is not None and not details_df.empty:
            details_df.to_excel(writer, sheet_name="Cost Details", index=False)
        # ورقة اللوجستيات
        if logistics_df is not None and not logistics_df.empty:
            logistics_df.to_excel(writer, sheet_name="Logistics", index=False)
        # ورقة ECD details
        pd.DataFrame([ecd_details]).to_excel(writer, sheet_name="ECD Details", index=False)
        # **لا تستدعي writer.save() هنا** — سياق with يتولى الإغلاق والكتابة
    output.seek(0)
    return output


def create_pdf_report(fig, ecd_total, ecd_details, total_cost, meta):
    # حفظ الشكل كصورة PNG في الذاكرة
    # يتطلب تثبيت kaleido أو دعم to_image
    try:
        img_bytes = fig.to_image(format="png", scale=2)
    except Exception:
        # محاولة بديلة: حفظ كـ static image عبر write_image (يتطلب kaleido)
        img_bytes = fig.to_image(format="png", scale=1)
    img = Image.open(io.BytesIO(img_bytes))
    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    png_buf.seek(0)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Drilling Fluid Report", ln=True, align="C")
    pdf.ln(4)
    pdf.set_font("Arial", size=11)
    # ملخص بسيط
    pdf.multi_cell(0, 6, f"Well: {meta.get('Well','-')}  |  Unit System: {meta.get('Unit System','-')}")
    pdf.multi_cell(0, 6, f"Target MW: {meta.get('Target MW','-')}  |  ECD: {ecd_total}")
    pdf.multi_cell(0, 6, f"Total Estimated Cost: ${total_cost:,.2f}")
    pdf.ln(6)
    # إدراج الصورة
    tmp_img = io.BytesIO(png_buf.getvalue())
    # FPDF expects a filename or file-like object; pass BytesIO
    pdf.image(tmp_img, x=15, w=180)
    # تفاصيل ΔP
    pdf.ln(6)
    pdf.set_font("Arial", size=10)
    pdf.multi_cell(0, 6, f"ECD Details: ΔP={ecd_details.get('deltaP_Pa')} Pa; ΔSG={ecd_details.get('delta_SG')}")
    out = io.BytesIO(pdf.output(dest='S').encode('latin-1'))
    out.seek(0)
    return out

# ---------------------------
# Sidebar (identity + upload inventory + upload offset wells)
# ---------------------------
with st.sidebar:
    st.image("https://media.licdn.com/dms/image/v2/D5603AQGFR4yZ0Xbu8g/profile-displayphoto-scale_400_400/B56Zxrp.xPKgAg-/0/1771332696780?e=1778112000&v=beta&t=tAmGd--8fgLzeWLXKFNGWQDkBBvcV-zxaRUUekQjEA0", width=120)
    col1, col2 = st.sidebar.columns([1, 3])
    with col1:
        st.image("https://media.licdn.com/dms/image/v2/D4D03AQH_gUWhtKDArA/profile-displayphoto-scale_400_400/B4DZxtywF.HwAg-/0/1771368594755?e=1778112000&v=beta&t=2zHjZvkL_46hw9zD8S40rnYdzkWgSINDA7aLZ_f8k8U", width=60)
    with col2:
        st.write("### المهندس عمار أسعد")
        st.write("مطور التطبيق")
    st.sidebar.markdown("---")

    # ---------------------------
    # رفع بيانات الآبار المجاورة (بدلاً من تحميل المثال تلقائياً)
    # ---------------------------
    st.sidebar.title("🗂️ بيانات الآبار المجاورة")
    st.sidebar.write("ارفع ملف CSV يحتوي أعمدة: Well_Name, Max_Depth_m, Max_MW, Mud_Type (اختياري), Issues (اختياري)")
    offset_file = st.sidebar.file_uploader("ارفع ملف بيانات الآبار المجاورة (CSV)", type="csv", key="offset")
    if offset_file is not None:
        try:
            df_offset = pd.read_csv(offset_file)
            ok, msg = validate_offset_df(df_offset)
            if ok:
                st.session_state['offset_data'] = df_offset
                st.sidebar.success("تم تحميل بيانات الآبار المجاورة.")
            else:
                st.sidebar.error(msg)
        except Exception as e:
            st.sidebar.error(f"خطأ في قراءة ملف الآبار: {e}")
    else:
        st.sidebar.info("لم يتم رفع ملف آبار مجاورة. يمكنك رفع CSV هنا.")
        st.sidebar.markdown("أو قم بتحميل ملف مثال لتعديل الهيكل ثم ارفعه مجدداً:")
        st.sidebar.download_button(
            label="📥 تحميل ملف CSV المرجعي (Sample)",
            data=get_sample_csv_bytes(),
            file_name="Sample_Offset_Wells.csv",
            mime="text/csv"
        )

    st.sidebar.markdown("---")
    st.sidebar.title("📦 مخزون الموقع (Site Inventory)")
    st.sidebar.write("يمكنك رفع CSV يحتوي أعمدة: Product, Qty, Unit (kg, lb, gal, MT)")
    inv_file = st.sidebar.file_uploader("ارفع ملف مخزون الموقع (CSV)", type="csv", key="inv")
    if inv_file is not None:
        try:
            df_inv = pd.read_csv(inv_file)
            # تنظيف أسماء الأعمدة
            df_inv.columns = [c.strip() for c in df_inv.columns]
            if {'Product','Qty','Unit'}.issubset(set(df_inv.columns)):
                st.session_state['site_inventory'] = df_inv
                st.sidebar.success("تم تحميل مخزون الموقع.")
            else:
                st.sidebar.error("الملف يجب أن يحتوي أعمدة: Product, Qty, Unit")
        except Exception as e:
            st.sidebar.error(f"خطأ في قراءة ملف المخزون: {e}")
    else:
        st.sidebar.info("يمكنك إدخال المخزون يدوياً أدناه.")
        if st.sidebar.button("إضافة مثال مخزون افتراضي"):
            st.session_state['site_inventory'] = pd.DataFrame({
                "Product": ["Barite", "Xanthan Gum", "PAC-R", "KCl"],
                "Qty": [20, 100, 50, 200],
                "Unit": ["1 MT", "25 kg", "25 kg", "50 lb"]
            })
            st.sidebar.success("تم إضافة مثال مخزون.")

    st.sidebar.markdown("---")
    st.sidebar.title("📩 تواصل معي")
    linkedin_url = "https://www.linkedin.com/in/ammar-asaad/"
    st.sidebar.markdown(f'[![LinkedIn](https://img.shields.io/badge/LinkedIn-Profile-blue?style=for-the-badge&logo=linkedin)]({linkedin_url})')
    email = "ammarasaad727@gmail.com"
    st.sidebar.write(f"📧: {email}")
    st.markdown("---")
    st.write("⚙️ **الإصدار:** v6.0 Enterprise")

# ---------------------------
# Main UI
# ---------------------------
st.title("🛢️ Advanced Drilling Fluid Expert & Simulator")
st.markdown("نظام ذكي متكامل لمحاكاة وتصميم سوائل الحفر بناءً على المعطيات الجيولوجية والديناميكية.")

tabs = st.tabs(["📋 إعدادات البئر", "🧬 التوصيات الهندسية", "📈 نافذة الضغوط", "📦 اللوجستيات والاقتصاد", "⚠️ المخاطر والتاريخ"])

with tabs[0]:
    st.markdown("### 🌍 المعطيات الأساسية للحقل")
    unit_system = st.radio("اختر نظام الوحدات (Unit System):", ["API (Imperial)", "SI (Metric)"], horizontal=True, key="unit_system")
    if unit_system == "API (Imperial)":
        depth_label, depth_unit = "العمق الإجمالي (ft):", "ft"
        hole_label, hole_unit = "قطر البئر (inch):", "in"
        flow_label, flow_unit = "معدل الضخ (GPM):", "GPM"
        press_label, press_unit = "الضغط (ppg):", "ppg"
        depth_val, hole_val, flow_val, pore_val, frac_val, margin_val = 10000.0, 12.25, 600.0, 9.5, 13.5, 0.5
    else:
        depth_label, depth_unit = "العمق الإجمالي (m):", "m"
        hole_label, hole_unit = "قطر البئر (cm):", "cm"
        flow_label, flow_unit = "معدل الضخ (L/min):", "L/min"
        press_label, press_unit = "الضغط (SG):", "SG"
        depth_val, hole_val, flow_val, pore_val, frac_val, margin_val = 3000.0, 31.1, 2200.0, 1.14, 1.62, 0.06

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        formation = st.selectbox("نوع التكوين الجيولوجي:", ["Sandstone (Clean)", "Reactive Shale", "Evaporites (Salt)", "High-Perm Carbonate"], key="formation")
        hole_size = st.number_input(hole_label, value=hole_val, key="hole_size")
        pipe_od = st.number_input("قطر أنبوب الحفر (Drillpipe OD)", value=5.0 if unit_system=="API (Imperial)" else 12.7, key="pipe_od")
    with col2:
        depth = st.number_input(depth_label, value=depth_val, step=100.0, key="depth")
        temp = st.slider("درجة الحرارة المتوقعة (°C):", 20, 200, 110, key="temp")
    with col3:
        pore_press = st.number_input(f"ضغط المسام ({press_unit}):", value=pore_val, step=0.1, key="pore_press")
        frac_press = st.number_input(f"ضغط الكسر ({press_unit}):", value=frac_val, step=0.1, key="frac_press")
        safety_margin = st.number_input(f"هامش الأمان ({press_unit}):", value=margin_val, step=0.05, key="safety_margin")
    with col4:
        pump_rate = st.number_input(flow_label, value=flow_val, step=50.0, key="pump_rate")
        has_h2s = st.checkbox("وجود غاز H2S محتمل؟", key="has_h2s")
        apparent_visc = st.number_input("الزوجة الظاهرية (cP) للتقدير", value=30.0, step=1.0, key="apparent_visc")

    target_mw = pore_press + safety_margin

with tabs[1]:
    if st.button("🚀 تشغيل محاكاة النظام الهندسي", type="primary"):
        st.session_state['analyzed'] = True
        # اختيار النظام والمنتجات الافتراضية
        if formation == "Reactive Shale" or temp > 150:
            system = "High-Performance Invert Emulsion (OBM/SBM)"
            why = "اختيار OBM لحماية الـ Shale واستقرار البئر في الحرارة العالية."
            pv, yp, fluid_loss = "15 - 25", "10 - 15", "< 4.0 cc"
            default_products = pd.DataFrame({
                "Product": ["Base Oil", "Primary Emulsifier", "Lime", "Organophilic Clay", "Barite"],
                "Conc (lb/bbl)": [0, 6.0, 4.0, 5.0, 150.0],
                "Unit Size": ["55 gal", "55 gal", "50 lb", "50 lb", "1 MT"],
                "Cost per Unit ($)": [200, 450, 10, 80, 120]
            })
        elif formation == "Evaporites (Salt)":
            system = "Saturated Brine WBM"
            why = "استخدام ملوحة مشبعة لمنع ذوبان الملح (Salt Creep)."
            pv, yp, fluid_loss = "12 - 20", "15 - 20", "< 6.0 cc"
            default_products = pd.DataFrame({
                "Product": ["NaCl Salt", "Xanthan Gum", "PAC-L", "Starch", "Barite"],
                "Conc (lb/bbl)": [120.0, 1.0, 2.0, 4.0, 50.0],
                "Unit Size": ["50 lb", "25 kg", "25 kg", "50 lb", "1 MT"],
                "Cost per Unit ($)": [5, 120, 90, 40, 120]
            })
        else:
            system = "Polymer Water-Based Mud (WBM)"
            why = "نظام اقتصادي وصديق للبيئة للطبقات المستقرة."
            pv, yp, fluid_loss = "10 - 18", "12 - 18", "< 5.0 cc"
            default_products = pd.DataFrame({
                "Product": ["Bentonite", "Xanthan Gum", "PAC-R", "KCl", "Barite"],
                "Conc (lb/bbl)": [15.0, 0.75, 1.5, 15.0, 20.0],
                "Unit Size": ["1 MT", "25 kg", "25 kg", "50 lb", "1 MT"],
                "Cost per Unit ($)": [100, 120, 95, 15, 120]
            })
        # حفظ النتائج في session_state
        st.session_state.update({
            'system': system,
            'why': why,
            'pv': pv,
            'yp': yp,
            'fl': fluid_loss,
            'products': default_products
        })

    if st.session_state['analyzed']:
        st.success(f"**النظام الموصى به:** {st.session_state.get('system')}")
        st.markdown(f"<div class='justification-box'>{st.session_state.get('why')}</div>", unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)

        # حساب ECD وحفظه في الحالة لضمان توفره في تبويبات أخرى
        ecd_total, ecd_details = compute_ecd_with_darcy(
            target_mw=target_mw,
            pump_rate=pump_rate,
            hole_id=hole_size,
            pipe_od=pipe_od,
            depth=depth,
            unit_system=unit_system,
            apparent_visc_cP=apparent_visc,
            mud_type=st.session_state.get('system', '')
        )
        st.session_state['ecd_total'] = ecd_total
        st.session_state['ecd_details'] = ecd_details

        c1.metric("كثافة الطفلة (MW)", f"{target_mw:.3f} {press_unit}")
        c2.metric("اللزوجة الظاهرية (cP)", f"{apparent_visc:.1f}", "cP")
        c3.metric("نقطة الخضوع (YP)", st.session_state.get('yp'), "lb/100ft²")
        c4.metric("الكثافة الدورانية (ECD)", f"{ecd_total:.3f} {press_unit}")
        st.markdown(f"**تفصيل خسارة الضغط:** ΔP={ecd_details['deltaP_Pa']} Pa; ΔMW(ppg)≈{ecd_details.get('delta_MW_ppg')}")

        st.markdown("#### 🔁 خيارات بديلة لسائل الحفر (حسب الملاءمة)")
        alternatives = MUD_ALTERNATIVES.get(st.session_state.get('system', ''), [])
        alt_df = pd.DataFrame([{"Alternative": a[0], "Suitability": a[1]} for a in alternatives])
        st.table(alt_df)

with tabs[2]:
    if st.session_state['analyzed']:
        # تأكد من وجود ecd_total قبل الاستخدام
        ecd_total = st.session_state.get('ecd_total')
        if ecd_total is None:
            st.warning("يرجى تشغيل المحاكاة أولاً لعرض نافذة الضغوط.")
        else:
            depths = np.linspace(0, depth, 50)
            fig = go.Figure()
            x_min = 8.0 if unit_system == "API (Imperial)" else 1.0
            fig.add_trace(go.Scatter(x=np.linspace(x_min, pore_press, 50), y=depths, mode='lines', name='Pore Pressure', line=dict(color='red')))
            fig.add_trace(go.Scatter(x=np.full_like(depths, target_mw), y=depths, mode='lines', name='MW', line=dict(color='blue', dash='dash')))
            fig.add_trace(go.Scatter(x=np.full_like(depths, ecd_total), y=depths, mode='lines', name='ECD', line=dict(color='orange')))
            fig.add_trace(go.Scatter(x=np.linspace(target_mw + (1.5 if unit_system=="API (Imperial)" else 0.18), frac_press, 50), y=depths, mode='lines', name='Frac Pressure', line=dict(color='green')))
            fig.update_layout(yaxis=dict(autorange="reversed", title=f"Depth ({depth_unit})"), xaxis=dict(title=f"Pressure ({press_unit})"), height=500)
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("شغّل المحاكاة في تبويب 'التوصيات الهندسية' لعرض نافذة الضغوط.")

with tabs[3]:
    if st.session_state['analyzed']:
        st.markdown("### 🧮 حساب الأحجام والتكلفة التفصيلية + لوجستيات")
        volume, vol_unit = compute_volume(hole_size, depth, unit_system)
        st.info(f"**حجم النظام الكلي المقدر (مع هامش سطحي 50%):** {volume} {vol_unit}")
        st.markdown("#### 🧪 المواد الكيميائية (قابل للتعديل)")

        # تأكد من وجود products في الحالة
        if st.session_state.get('products') is None:
            st.warning("لا توجد قائمة منتجات. شغّل المحاكاة أو أدخل المنتجات يدوياً.")
            edited_df = pd.DataFrame(columns=["Product", "Conc (lb/bbl)", "Unit Size", "Cost per Unit ($)"])
        else:
            edited_df = st.data_editor(st.session_state['products'], num_rows="dynamic", use_container_width=True)

        total_cost, details_df = estimate_units_and_cost_precise(edited_df, volume, vol_unit)
        c1, c2 = st.columns(2)
        c1.metric(f"إجمالي الحجم ({vol_unit})", f"{volume}")
        c2.metric("التكلفة الإجمالية التقديرية", f"${total_cost:,.2f}")
        st.markdown("#### تفاصيل التكلفة (تقديري)")
        st.dataframe(details_df, use_container_width=True)

        st.markdown("#### 📦 مقارنة مع مخزون الموقع وحساب اللوجستيات")
        logistics_df = pd.DataFrame()  # default empty
        if st.session_state['site_inventory'] is None:
            st.warning("لم يتم تعريف مخزون الموقع. يمكنك رفع ملف CSV في الشريط الجانبي أو إضافة مثال.")
        else:
            inv = st.session_state['site_inventory'].copy()
            st.write("مخزون الموقع:")
            st.dataframe(inv, use_container_width=True)

            def qty_to_kg(qty, unit):
                # استخدام parse_unit_size_to_qty لتحويل الوحدات قدر الإمكان
                try:
                    qty_val, qty_unit = parse_unit_size_to_qty(f"{qty} {unit}")
                    if qty_val is None:
                        return 0.0
                    if qty_unit == 'kg':
                        return qty_val
                    if qty_unit == 'lb':
                        return qty_val * 0.45359237
                    if qty_unit == 'gal':
                        # افتراض كثافة 1 kg/L => 3.78541 kg/gal (قابل للتعديل لاحقاً)
                        return qty_val * 3.78541
                    # إذا كانت الوحدة None ولكن النص يحتوي 'mt' أو 'ton'
                    s = str(unit).lower()
                    if 'mt' in s or 'ton' in s or 'tonne' in s:
                        return qty_val * 1000.0
                    return qty_val
                except Exception:
                    return 0.0

            inv_lookup = {}
            for _, r in inv.iterrows():
                name = str(r['Product']).strip()
                qty = r['Qty']
                unit = r['Unit']
                inv_lookup[name.lower()] = inv_lookup.get(name.lower(), 0.0) + qty_to_kg(qty, unit)

            logistics_rows = []
            for _, prod in edited_df.iterrows():
                pname = prod['Product']
                conc = float(prod.get('Conc (lb/bbl)', 0) or 0)
                unit_size = prod.get('Unit Size', '')
                qty_val, qty_unit = parse_unit_size_to_qty(unit_size)
                if vol_unit == 'bbl':
                    total_mass_lb = conc * volume
                    total_mass_kg = total_mass_lb * 0.45359237
                else:
                    conc_kg_per_m3 = conc * 0.45359237 / 0.158987
                    total_mass_kg = conc_kg_per_m3 * volume

                available_kg = inv_lookup.get(str(pname).lower(), 0.0)
                shortfall_kg = max(0.0, total_mass_kg - available_kg)
                if qty_unit == 'kg' and qty_val:
                    units_to_order = shortfall_kg / qty_val
                elif qty_unit == 'lb' and qty_val:
                    units_to_order = (shortfall_kg / 0.45359237) / qty_val
                elif qty_unit == 'gal' and qty_val:
                    units_to_order = shortfall_kg / (3.78541 * qty_val)
                else:
                    units_to_order = shortfall_kg / 50.0

                cost_per_unit = float(prod.get('Cost per Unit ($)', 0) or 0)
                est_purchase_cost = max(0, units_to_order) * cost_per_unit
                est_shipping_cost = (shortfall_kg / 1000.0) * 200.0 + (50.0 if units_to_order > 0 else 0.0)
                total_log_cost = est_purchase_cost + est_shipping_cost

                logistics_rows.append({
                    "Product": pname,
                    "Required_kg": round(total_mass_kg,2),
                    "Available_kg": round(available_kg,2),
                    "Shortfall_kg": round(shortfall_kg,2),
                    "Units_to_Order": round(units_to_order,2),
                    "Est_Purchase_Cost($)": round(est_purchase_cost,2),
                    "Est_Shipping_Cost($)": round(est_shipping_cost,2),
                    "Total_Logistics_Cost($)": round(total_log_cost,2)
                })

            logistics_df = pd.DataFrame(logistics_rows)
            st.markdown("تفاصيل اللوجستيات (تقديري)")
            st.dataframe(logistics_df, use_container_width=True)
            st.metric("التكلفة اللوجستية الإجمالية التقديرية", f"${logistics_df['Total_Logistics_Cost($)'].sum():,.2f}")

        # ---------------------------
        # أزرار التصدير (Excel و PDF)
        # ---------------------------
        if 'fig' not in locals():
            # حاول إعادة إنشاء شكل نافذة الضغوط إن لم يكن موجودًا في الذاكرة
            depths = np.linspace(0, depth, 50)
            fig = go.Figure()
            x_min = 8.0 if unit_system == "API (Imperial)" else 1.0
            fig.add_trace(go.Scatter(x=np.linspace(x_min, pore_press, 50), y=depths, mode='lines', name='Pore Pressure', line=dict(color='red')))
            fig.add_trace(go.Scatter(x=np.full_like(depths, target_mw), y=depths, mode='lines', name='MW', line=dict(color='blue', dash='dash')))
            fig.add_trace(go.Scatter(x=np.full_like(depths, st.session_state['ecd_total']), y=depths, mode='lines', name='ECD', line=dict(color='orange')))
            fig.add_trace(go.Scatter(x=np.linspace(target_mw + (1.5 if unit_system=="API (Imperial)" else 0.18), frac_press, 50), y=depths, mode='lines', name='Frac Pressure', line=dict(color='green')))
            fig.update_layout(yaxis=dict(autorange="reversed", title=f"Depth ({depth_unit})"), xaxis=dict(title=f"Pressure ({press_unit})"), height=500)

        meta = {
            "Well": "Well-1",
            "Unit System": unit_system,
            "Hole Size": hole_size,
            "Depth": depth,
            "Pump Rate": pump_rate,
            "Target MW": target_mw
        }

        excel_bytes = create_excel_report(st.session_state['ecd_total'], st.session_state['ecd_details'],
                                          details_df if 'details_df' in locals() else None,
                                          logistics_df if 'logistics_df' in locals() else logistics_df,
                                          meta)
        pdf_bytes = create_pdf_report(fig, st.session_state['ecd_total'], st.session_state['ecd_details'],
                                      total_cost if 'total_cost' in locals() else total_cost, meta)

        col_a, col_b = st.columns([1,1])
        with col_a:
            st.download_button("⬇️ تنزيل تقرير Excel", data=excel_bytes, file_name="Drilling_Report.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with col_b:
            st.download_button("⬇️ تنزيل تقرير PDF", data=pdf_bytes, file_name="Drilling_Report.pdf", mime="application/pdf")

with tabs[4]:
    if st.session_state['analyzed']:
        if has_h2s:
            st.error("🚨 خطر H2S: أضف Zinc Carbonate أو Iron Sponge فوراً!")
        st.markdown("### 📊 بيانات الآبار المجاورة (Offset Wells)")
        # عرض بيانات الآبار المجاورة إذا تم رفعها
        if st.session_state['offset_data'] is not None:
            df = st.session_state['offset_data']
            st.dataframe(df, use_container_width=True)
            if 'Max_MW' in df.columns:
                try:
                    max_mw = df['Max_MW'].astype(float).max()
                    if target_mw > max_mw:
                        st.markdown(f"<div class='risk-high'>🚨 <strong>تحذير هندسي:</strong> الكثافة المقترحة ({target_mw:.2f} {press_unit}) أعلى من أقصى كثافة مستخدمة سابقاً ({max_mw} {press_unit}). خطر فقدان الدورة!</div>", unsafe_allow_html=True)
                    else:
                        st.success(f"✅ الكثافة المقترحة ({target_mw:.2f} {press_unit}) تعتبر آمنة بناءً على تاريخ الآبار المجاورة.")
                except Exception:
                    st.info("تعذر مقارنة القيم الرقمية في عمود 'Max_MW'. تأكد من أن القيم رقمية.")
            else:
                st.info("💡 يرجى التأكد من وجود عمود باسم 'Max_MW' لإجراء المقارنة التلقائية للكثافة.")
        else:
            st.warning("⚠️ لم يتم رفع بيانات آبار مجاورة. يمكنك رفع ملف الـ Sample من القائمة الجانبية لتجربة الأداة.")

# Footer
st.markdown("---")
st.caption("Developed by Ammar Asaad | The Digital Petroleum Engineer | © 2026")

