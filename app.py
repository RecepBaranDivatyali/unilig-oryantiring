import streamlit as st
import pandas as pd
import pdfplumber
import io
import os
import numpy as np
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# --- Yapılandırma ve Stil ---
st.set_page_config(page_title="Ünilig Oryantiring", page_icon="🏃‍♂️", layout="wide")

st.markdown("""
<style>
    .metric-container { background-color: #f0f2f6; padding: 15px; border-radius: 10px; text-align: center; box-shadow: 2px 2px 5px rgba(0,0,0,0.1); margin-bottom: 15px;}
    .metric-value { font-size: 24px; font-weight: bold; color: #1f77b4; }
    .metric-label { font-size: 14px; color: #555; }
    .stDataFrame { width: 100%; }
</style>
""", unsafe_allow_html=True)

DATA_FILE = "orienteering_data.csv"
ADMIN_PASSWORD = "odtu2026"

# --- Yardımcı Fonksiyonlar ---
def time_to_seconds(t_str):
    if pd.isna(t_str) or str(t_str).strip() == "": return 0
    try:
        parts = str(t_str).strip().split(':')
        if len(parts) == 2: return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3: return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except: pass
    return 0

def seconds_to_time(sec):
    if sec <= 0 or pd.isna(sec): return ""
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def load_data():
    if os.path.exists(DATA_FILE):
        df = pd.read_csv(DATA_FILE, dtype={'Göğüs No': str, '1. Gün Süresi': str, '2. Gün Süresi': str, '1. Gün Çıkış': str, '2. Gün Çıkış': str})
        # Gerekli sütunları garantiye al
        expected_cols = ['Göğüs No', 'İsim', 'Üniversite', 'Kategori', '1. Gün Çıkış', '2. Gün Çıkış', '1. Gün Süresi', '2. Gün Süresi', 'Durum']
        for col in expected_cols:
            if col not in df.columns: 
                # Eski sürümden geçiş için 'Çıkış Saati' varsa '1. Gün Çıkış'a aktar
                if col == '1. Gün Çıkış' and 'Çıkış Saati' in df.columns:
                    df['1. Gün Çıkış'] = df['Çıkış Saati']
                else:
                    df[col] = ""
        return df
    else:
        return pd.DataFrame(columns=['Göğüs No', 'İsim', 'Üniversite', 'Kategori', '1. Gün Çıkış', '2. Gün Çıkış', '1. Gün Süresi', '2. Gün Süresi', 'Durum'])

def save_data(df):
    df.to_csv(DATA_FILE, index=False)

if 'df' not in st.session_state:
    st.session_state.df = load_data()

# --- File uploader key sayaçları (sıfırlamak için) ---
if 'key_start' not in st.session_state:
    st.session_state.key_start = 0
if 'key_res1' not in st.session_state:
    st.session_state.key_res1 = 0
if 'key_res2' not in st.session_state:
    st.session_state.key_res2 = 0

def update_data(new_df):
    st.session_state.df = new_df.copy()
    save_data(st.session_state.df)

TOF_LIVE_URL = "https://www.oryantiring.org.tr/uploads/livelocals/result_kastamonu.html"

def fetch_live_results():
    """TOF canlı sonuç sayfasından göğüs no ve süre verilerini çeker."""
    try:
        r = requests.get(TOF_LIVE_URL, timeout=10)
        # Türkçe karakter desteği için kodlamayı ayarla
        r.encoding = r.apparent_encoding if r.apparent_encoding else 'windows-1254'
        soup = BeautifulSoup(r.content, 'html.parser')
        
        data = []
        valid_statuses = ['mp', 'dnf', 'dns', 'dsq']
        
        rows = soup.find_all('tr')
        for row in rows:
            cols = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cols) >= 5:
                # Sıra | No | Adı Soyadı | Kulübü | Süre | ...
                # Sıra sütunu sayı mı?
                if cols[0].isdigit() and cols[1].isdigit():
                    bib = cols[1]
                    sure = cols[4] if len(cols) > 4 else ''
                    if sure:
                        data.append({'Göğüs No': bib, 'Süre': sure})
                # MP/DNF/DNS/DSQ durumları — sıra yerine durum kodu olabilir
                elif cols[1].isdigit() and cols[-1].lower() in valid_statuses:
                    bib = cols[1]
                    sure = cols[-1].upper()
                    data.append({'Göğüs No': bib, 'Süre': sure})
        
        return pd.DataFrame(data) if data else pd.DataFrame(columns=['Göğüs No', 'Süre'])
    except Exception as e:
        return str(e)

# --- PDF İşleme Fonksiyonları ---
def parse_start_list(file):
    data = []
    current_univ = ""
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                for line in text.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                        
                    parts = line.split()
                    
                    if not ":" in line and len(parts) >= 2 and parts[0].isdigit():
                        raw_univ = " ".join(parts[1:])
                        raw_univ = re.sub(r'\(?devam[ıi]\)?', '', raw_univ, flags=re.IGNORECASE)
                        current_univ = re.sub(r'[\(\)\d\s]+$', '', raw_univ).strip()
                        continue
                        
                    # S1 ve S2 sütunlarını kontrol et
                    if len(parts) >= 5 and ":" in parts[-1]:
                        s1_time = ""
                        s2_time = ""
                        kat = ""
                        isim_bitis = -1
                        
                        # Eğer son iki sütun da saatse (S1 ve S2)
                        if ":" in parts[-1] and ":" in parts[-2]:
                            s1_time = parts[-2]
                            s2_time = parts[-1]
                            kat = parts[-3]
                            isim_bitis = -3
                        else:
                            # Sadece bir saat varsa (muhtemelen S1 veya tek etap)
                            s1_time = parts[-1]
                            s2_time = ""
                            kat = parts[-2]
                            isim_bitis = -2
                            
                        bib = parts[1] if parts[1].isdigit() else parts[0]
                        
                        if bib.isdigit():
                            isim_baslangic = 3 if (len(parts) > 3 and parts[2].isdigit()) else 2
                            isim = " ".join(parts[isim_baslangic:isim_bitis])
                            
                            data.append({
                                'Göğüs No': str(bib), 'İsim': isim, 'Üniversite': current_univ, 
                                'Kategori': kat, '1. Gün Çıkış': s1_time, '2. Gün Çıkış': s2_time,
                                '1. Gün Süresi': "", '2. Gün Süresi': "", 'Durum': 'Bekliyor'
                            })
    return pd.DataFrame(data)

def parse_results(file):
    data = []
    valid_statuses = ['mp', 'dnf', 'dns', 'dsq']
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                for line in text.split('\n'):
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        last_part = parts[-1].lower()
                        prev_part = parts[-2].lower() if len(parts) > 1 else ""
                        
                        if last_part.startswith('+') or last_part == '0:00' or (':' in last_part and ':' in prev_part):
                            time_str = parts[-2]
                        else:
                            time_str = parts[-1]
                            
                        time_lower = time_str.lower()
                        if time_lower in valid_statuses or ":" in time_lower:
                            bib = parts[1] if parts[0].isdigit() else parts[0]
                            if bib.isdigit():
                                data.append({'Göğüs No': str(bib), 'Süre': time_str.upper()})
    return pd.DataFrame(data)

# --- Veri Hazırlama Fonksiyonları ---
def prepare_data(df):
    df_calc = df.copy()
    for day in ['1. Gün', '2. Gün']:
        df_calc[f'{day} Süresi_sec'] = df_calc[f'{day} Süresi'].apply(time_to_seconds)
    return df_calc

def get_team_scores(df_calc, day_col):
    sec_col = f'{day_col} Süresi_sec'
    if sec_col not in df_calc.columns: return pd.DataFrame()
    
    # Ferdi kategorileri puanlamaya dahil edilmez
    df_calc = df_calc[~df_calc['Kategori'].str.upper().str.contains('FERDİ', na=False)]
    
    team_scores = []
    for kat in df_calc['Kategori'].dropna().unique():
        for univ in df_calc['Üniversite'].dropna().unique():
            univ_df = df_calc[(df_calc['Üniversite'] == univ) & (df_calc['Kategori'] == kat) & (df_calc[sec_col] > 0)]
            if len(univ_df) >= 3:
                top_3 = univ_df.nsmallest(3, sec_col)
                total_sec = top_3[sec_col].sum()
                team_scores.append({'Üniversite': univ, 'Kategori': kat, 'Toplam Süre (sn)': total_sec, 'Toplam Süre': seconds_to_time(total_sec)})
    if team_scores:
        return pd.DataFrame(team_scores).sort_values(by=['Kategori', 'Toplam Süre (sn)'], ascending=[True, True]).drop(columns=['Toplam Süre (sn)']).reset_index(drop=True)
    return pd.DataFrame(columns=['Üniversite', 'Kategori', 'Toplam Süre'])

def get_individual_scores(df_calc, day_col):
    sec_col = f'{day_col} Süresi_sec'
    if sec_col not in df_calc.columns: return pd.DataFrame()
    ind_df = df_calc[(df_calc[sec_col] > 0)].copy()
    if ind_df.empty: return pd.DataFrame(columns=['Göğüs No', 'İsim', 'Üniversite', 'Kategori', 'Süre'])
    ind_df['Süre'] = ind_df[sec_col].apply(seconds_to_time)
    ind_df = ind_df.sort_values(by=sec_col, ascending=True)
    return ind_df[['Göğüs No', 'İsim', 'Üniversite', 'Kategori', 'Süre']]

def get_general_individual_scores(df_calc):
    if '1. Gün Süresi_sec' not in df_calc.columns or '2. Gün Süresi_sec' not in df_calc.columns: return pd.DataFrame()
    ind_df = df_calc[(df_calc['1. Gün Süresi_sec'] > 0) & (df_calc['2. Gün Süresi_sec'] > 0)].copy()
    if ind_df.empty: return pd.DataFrame(columns=['Göğüs No', 'İsim', 'Üniversite', 'Kategori', 'Genel Süre'])
    ind_df['Genel Süresi_sec'] = ind_df['1. Gün Süresi_sec'] + ind_df['2. Gün Süresi_sec']
    ind_df['Genel Süre'] = ind_df['Genel Süresi_sec'].apply(seconds_to_time)
    ind_df = ind_df.sort_values(by='Genel Süresi_sec', ascending=True)
    return ind_df[['Göğüs No', 'İsim', 'Üniversite', 'Kategori', 'Genel Süre']]



def update_dynamic_status(df, current_day=1):
    df_dyn = df.copy()
    current_time_str = datetime.now().strftime("%H:%M:%S")
    current_sec = time_to_seconds(current_time_str)
    
    col_name = f"{current_day}. Gün Çıkış"
    if col_name not in df_dyn.columns: return df_dyn
    
    mask = ~df_dyn['Durum'].isin(['Tamamladı', 'MP', 'DNF', 'DNS', 'DSQ'])
    if mask.any():
        for idx, row in df_dyn[mask].iterrows():
            cikis_saati = str(row[col_name]).strip()
            if cikis_saati and ':' in cikis_saati:
                cikis_sec = time_to_seconds(cikis_saati)
                if current_sec >= cikis_sec:
                    df_dyn.at[idx, 'Durum'] = 'Parkurda'
                else:
                    df_dyn.at[idx, 'Durum'] = 'Bekliyor'
            else:
                df_dyn.at[idx, 'Durum'] = 'Bekliyor'
    return df_dyn

# --- ANA EKRAN / SEKMELER ---
tab1, tab2 = st.tabs(["👁️ İzleyici Paneli", "⚙️ Admin Paneli"])

with tab1:
    st_autorefresh(interval=60000, key="data_refresh")
    st.title("🏆 Ünilig Oryantiring Canlı Sonuçlar")
    
    # Gün seçimi (Bekleyenler listesi ve durum takibi için)
    current_race_day = st.sidebar.selectbox("⏱️ Takip Edilen Gün", [1, 2], index=0, help="Parkurda/Bekliyor durumu hangi günün çıkış saatine göre hesaplansın?")
    
    # Veriyi hesapla (Artık gün seçimine göre)
    df_current = update_dynamic_status(st.session_state.df, current_race_day)
    df_scored = prepare_data(df_current) if not df_current.empty else pd.DataFrame()
    
    if not df_scored.empty:
        categories = [k for k in df_scored['Kategori'].dropna().unique() if 'KADIN' in str(k).upper() or 'ERKEK' in str(k).upper()]
        categories = sorted(categories)
        
        df_team_day1 = get_team_scores(df_scored, '1. Gün')
        df_team_day2 = get_team_scores(df_scored, '2. Gün')
        df_ind_day1 = get_individual_scores(df_scored, '1. Gün')
        df_ind_day2 = get_individual_scores(df_scored, '2. Gün')
        df_ind_genel = get_general_individual_scores(df_scored)
        
        genel_team_scores = []
        if not df_team_day1.empty or not df_team_day2.empty:
            # Ferdi olmayan sporcuları filtrele
            df_non_ferdi = df_scored[~df_scored['Kategori'].str.upper().str.contains('FERDİ', na=False)]
            for kat in categories:
                for univ in df_non_ferdi['Üniversite'].dropna().unique():
                    univ_day1 = df_non_ferdi[(df_non_ferdi['Üniversite'] == univ) & (df_non_ferdi['Kategori'] == kat) & (df_non_ferdi['1. Gün Süresi_sec'] > 0)]
                    univ_day2 = df_non_ferdi[(df_non_ferdi['Üniversite'] == univ) & (df_non_ferdi['Kategori'] == kat) & (df_non_ferdi['2. Gün Süresi_sec'] > 0)]
                    
                    # Genel takım sıralaması için her iki günde de en az 3 sporcu olması gerekir
                    if len(univ_day1) >= 3 and len(univ_day2) >= 3:
                        t1 = univ_day1.nsmallest(3, '1. Gün Süresi_sec')['1. Gün Süresi_sec'].sum()
                        t2 = univ_day2.nsmallest(3, '2. Gün Süresi_sec')['2. Gün Süresi_sec'].sum()
                        total_sec = t1 + t2
                        genel_team_scores.append({'Üniversite': univ, 'Kategori': kat, 'Genel Toplam Süre (sn)': total_sec, 'Genel Toplam Süre': seconds_to_time(total_sec)})
        df_team_genel = pd.DataFrame(genel_team_scores)

        categories_with_default = ["Seçiniz..."] + categories
        selected_kat = st.radio("📌 **Lütfen Sıralamasını Görmek İstediğiniz Kategoriyi Seçiniz:**", categories_with_default, horizontal=True)
        st.markdown("<hr style='border: 2px solid #ccc; margin: 10px 0 30px 0;'>", unsafe_allow_html=True)
        
        if selected_kat == "Seçiniz...":
            st.markdown("### 🏆 TAKIM SIRALAMALARI ÖZETİ")
            summary_col1, summary_col2 = st.columns(2)
            
            with summary_col1:
                st.markdown("<h4 style='text-align: center; color: #ff4b4b;'>👩‍🎓 KADINLAR TAKIM</h4>", unsafe_allow_html=True)
                # Genel varsa genel, yoksa 1. gün
                kadın_kat = next((k for k in categories if "KADIN" in k.upper()), None)
                if kadın_kat:
                    if not df_team_genel.empty and kadın_kat in df_team_genel['Kategori'].values:
                        st.caption("Genel Toplam (1.+2. Gün)")
                        df_show = df_team_genel[df_team_genel['Kategori'] == kadın_kat].sort_values(by='Genel Toplam Süre (sn)').drop(columns=['Kategori', 'Genel Toplam Süre (sn)']).reset_index(drop=True)
                        df_show.index += 1
                        st.dataframe(df_show, use_container_width=True)
                    elif not df_team_day1.empty and kadın_kat in df_team_day1['Kategori'].values:
                        st.caption("1. Gün Sıralaması")
                        df_show = df_team_day1[df_team_day1['Kategori'] == kadın_kat].drop(columns=['Kategori']).reset_index(drop=True)
                        df_show.index += 1
                        st.dataframe(df_show, use_container_width=True)
                    else: st.info("Henüz veri yok.")
                else: st.info("Kategori bulunamadı.")
                
            with summary_col2:
                st.markdown("<h4 style='text-align: center; color: #1f77b4;'>👨‍🎓 ERKEKLER TAKIM</h4>", unsafe_allow_html=True)
                erkek_kat = next((k for k in categories if "ERKEK" in k.upper()), None)
                if erkek_kat:
                    if not df_team_genel.empty and erkek_kat in df_team_genel['Kategori'].values:
                        st.caption("Genel Toplam (1.+2. Gün)")
                        df_show = df_team_genel[df_team_genel['Kategori'] == erkek_kat].sort_values(by='Genel Toplam Süre (sn)').drop(columns=['Kategori', 'Genel Toplam Süre (sn)']).reset_index(drop=True)
                        df_show.index += 1
                        st.dataframe(df_show, use_container_width=True)
                    elif not df_team_day1.empty and erkek_kat in df_team_day1['Kategori'].values:
                        st.caption("1. Gün Sıralaması")
                        df_show = df_team_day1[df_team_day1['Kategori'] == erkek_kat].drop(columns=['Kategori']).reset_index(drop=True)
                        df_show.index += 1
                        st.dataframe(df_show, use_container_width=True)
                    else: st.info("Henüz veri yok.")
                else: st.info("Kategori bulunamadı.")
                
            st.info("💡 Bireysel sonuçlar ve detaylı analizler için yukarıdan kategori seçebilirsiniz.")
        else:
            kat = selected_kat
            
            st.markdown(f"<h2 style='text-align: center; color: #4CAF50;'>🏃‍♂️ {kat} KATEGORİSİ 🏃‍♀️</h2>", unsafe_allow_html=True)
            
            st.markdown("### 🏆 TAKIM SIRALAMALARI")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown("**🥇 1. Gün Takım**")
                if not df_team_day1.empty and kat in df_team_day1['Kategori'].values:
                    df_show = df_team_day1[df_team_day1['Kategori'] == kat].drop(columns=['Kategori']).reset_index(drop=True)
                    df_show.index += 1
                    st.dataframe(df_show, use_container_width=True)
                else: st.info("Sonuç yok.")
            with col2:
                st.markdown("**🥈 2. Gün Takım**")
                if not df_team_day2.empty and kat in df_team_day2['Kategori'].values:
                    df_show = df_team_day2[df_team_day2['Kategori'] == kat].drop(columns=['Kategori']).reset_index(drop=True)
                    df_show.index += 1
                    st.dataframe(df_show, use_container_width=True)
                else: st.info("Sonuç yok.")
            with col3:
                st.markdown("**🏆 Genel Takım**")
                if not df_team_genel.empty and kat in df_team_genel['Kategori'].values:
                    df_show = df_team_genel[df_team_genel['Kategori'] == kat].sort_values(by='Genel Toplam Süre (sn)').drop(columns=['Kategori', 'Genel Toplam Süre (sn)']).reset_index(drop=True)
                    df_show.index += 1
                    st.dataframe(df_show, use_container_width=True)
                else: st.info("Sonuç yok.")
                
            st.markdown("### 👤 BİREYSEL SIRALAMALAR")
            col4, col5, col6 = st.columns(3)
            with col4:
                st.markdown("**🥇 1. Gün Bireysel**")
                if not df_ind_day1.empty and kat in df_ind_day1['Kategori'].values:
                    df_show = df_ind_day1[df_ind_day1['Kategori'] == kat].drop(columns=['Kategori', 'Göğüs No']).reset_index(drop=True)
                    df_show.index += 1
                    st.dataframe(df_show, use_container_width=True)
                else: st.info("Sonuç yok.")
            with col5:
                st.markdown("**🥈 2. Gün Bireysel**")
                if not df_ind_day2.empty and kat in df_ind_day2['Kategori'].values:
                    df_show = df_ind_day2[df_ind_day2['Kategori'] == kat].drop(columns=['Kategori', 'Göğüs No']).reset_index(drop=True)
                    df_show.index += 1
                    st.dataframe(df_show, use_container_width=True)
                else: st.info("Sonuç yok.")
            with col6:
                st.markdown("**🏆 Genel Bireysel**")
                if not df_ind_genel.empty and kat in df_ind_genel['Kategori'].values:
                    df_show = df_ind_genel[df_ind_genel['Kategori'] == kat].drop(columns=['Kategori', 'Göğüs No']).reset_index(drop=True)
                    df_show.index += 1
                    st.dataframe(df_show, use_container_width=True)
                else: st.info("Sonuç yok.")

        st.divider()
        st.subheader("⏳ ODTÜ Sporcuları (Takip Paneli)")
        st.caption(f"{current_race_day}. Gün Çıkış Listesine Göre")
        
        # ODTÜ filtresi ve Bekleyenler
        odtü_mask = df_scored['Üniversite'].str.contains('ORTA DOĞU TEKNİK', na=False, case=False)
        bekleyenler = df_scored[odtü_mask & df_scored['Durum'].isin(['Parkurda', 'Bekliyor', 'Parkurda/Bekliyor'])].copy()
        
        cikis_col = f'{current_race_day}. Gün Çıkış'
        bekleyenler['cikis_sec'] = bekleyenler[cikis_col].apply(time_to_seconds)
        bekleyenler = bekleyenler.sort_values(by='cikis_sec', ascending=True)
        
        st.dataframe(bekleyenler[['Göğüs No', 'İsim', 'Kategori', cikis_col, 'Durum']], use_container_width=True, hide_index=True)
        
        hatalilar_df = df_scored[df_scored['Durum'].isin(['MP', 'DNF', 'DNS', 'DSQ'])]
        if not hatalilar_df.empty:
            st.divider()
            st.subheader("❌ Geçersiz Çıkış / Diskalifiye (MP, DNF, DNS, DSQ)")
            st.dataframe(hatalilar_df[['Göğüs No', 'İsim', 'Üniversite', 'Kategori', '1. Gün Süresi', '2. Gün Süresi', 'Durum']], use_container_width=True, hide_index=True)
        
        st.divider()
        st.subheader("📊 İstatistikler ve Tahminler")
        
        df_stat = df_scored[df_scored['Kategori'] == selected_kat] if selected_kat != "Seçiniz..." else df_scored
        
        if not df_stat.empty:
            toplam = len(df_stat)
            tamamlayan = len(df_stat[df_stat['Durum'] == 'Tamamladı'])
            bekleyen_sayi = len(df_stat[df_stat['Durum'].isin(['Parkurda', 'Bekliyor', 'Parkurda/Bekliyor'])])
            hatali = len(df_stat[df_stat['Durum'].isin(['MP', 'DNF', 'DNS', 'DSQ'])])
            
            c1, c2, c3, c4 = st.columns(4)
            c1.markdown(f"<div class='metric-container'><div class='metric-label'>🏃 Toplam Sporcu</div><div class='metric-value'>{toplam}</div></div>", unsafe_allow_html=True)
            c2.markdown(f"<div class='metric-container'><div class='metric-label'>✅ Tamamlayan</div><div class='metric-value'>{tamamlayan}</div></div>", unsafe_allow_html=True)
            c3.markdown(f"<div class='metric-container'><div class='metric-label'>⏳ Parkurda / Bekleyen</div><div class='metric-value'>{bekleyen_sayi}</div></div>", unsafe_allow_html=True)
            c4.markdown(f"<div class='metric-container'><div class='metric-label'>❌ Geçersiz (MP vb.)</div><div class='metric-value'>{hatali}</div></div>", unsafe_allow_html=True)

            st.markdown("#### ⏱️ Hız Analizi ve Bitiş Tahmini")
            c_t1, c_t2, c_t3 = st.columns(3)
            
            avg_1 = df_stat[df_stat['1. Gün Süresi_sec'] > 0]['1. Gün Süresi_sec'].mean()
            avg_2 = df_stat[df_stat['2. Gün Süresi_sec'] > 0]['2. Gün Süresi_sec'].mean()
            
            with c_t1:
                if pd.notna(avg_1) and avg_1 > 0:
                    st.info(f"**🥇 1. Gün Ortalama Süre:** {seconds_to_time(avg_1)}")
                else:
                    st.info("**🥇 1. Gün Ortalama Süre:** Veri Yok")
                    
            with c_t2:
                if pd.notna(avg_2) and avg_2 > 0:
                    st.info(f"**🥈 2. Gün Ortalama Süre:** {seconds_to_time(avg_2)}")
                else:
                    st.info("**🥈 2. Gün Ortalama Süre:** Veri Yok")
                    
            with c_t3:
                cikis_col_stat = f'{current_race_day}. Gün Çıkış'
                valid_starts = [s for s in df_stat[cikis_col_stat].dropna().astype(str) if ':' in s]
                if valid_starts:
                    last_start_str = max(valid_starts)
                    last_sec = time_to_seconds(last_start_str)
                    valid_avgs = [a for a in [avg_1, avg_2] if pd.notna(a) and a > 0]
                    if valid_avgs:
                        overall_avg = sum(valid_avgs) / len(valid_avgs)
                        est_finish = last_sec + overall_avg
                        st.success(f"**🏁 Tahmini Yarış Bitişi:** {seconds_to_time(est_finish)}")
                    else:
                        st.success("**🏁 Tahmini Yarış Bitişi:** Ortalama Bekleniyor")
                else:
                    st.success("**🏁 Tahmini Yarış Bitişi:** Çıkış Saati Yok")
                    
    else:
        st.info("Henüz veri yüklenmedi. Lütfen Admin panelinden çıkış listesini yükleyin.")

with tab2:
    st.title("⚙️ Admin Paneli")
    admin_pass = st.text_input("Admin Şifresi", type="password")
    
    if admin_pass == ADMIN_PASSWORD:
        st.success("Admin paneline giriş yapıldı.")
        
        st.divider()
        st.subheader("1. Çıkış Listesi (Start List) Yükleme")
        st.info("Sabah yarıştan önce çıkış listesi PDF'ini buradan yükleyin. Herkes 'Parkurda/Bekliyor' olarak ayarlanır.")
        start_file = st.file_uploader("Çıkış Listesi PDF Yükle", type=['pdf'], key=f'start_list_{st.session_state.key_start}')
        if start_file and st.button("Çıkış Listesini İşle"):
            new_data = parse_start_list(start_file)
            if not new_data.empty:
                update_data(new_data)
                st.session_state.key_start += 1
                st.success(f"{len(new_data)} sporcu başarıyla eklendi!")
                st.rerun()
            else:
                st.error("PDF okunamadı. Formatın standart OE formatında olduğuna emin olun.")

        st.divider()
        st.subheader("🔴 2. Canlı Sonuç Çek (TOF Sistemi)")
        st.info("Türkiye Oryantiring Federasyonu'nun canlı sonuç sayfasından verileri otomatik çeker ve sisteminizi günceller. PDF yüklemenize gerek kalmaz.")
        
        live_col1, live_col2 = st.columns(2)
        with live_col1:
            live_gun = st.selectbox("Hangi güne işlensin?", ["1. Gün Süresi", "2. Gün Süresi"], key='live_gun')
        with live_col2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🔴 Canlı Sonuçları Çek ve Güncelle", use_container_width=True):
                with st.spinner("TOF sitesinden veriler çekiliyor..."):
                    res_df = fetch_live_results()
                
                if isinstance(res_df, str):
                    st.error(f"Bağlantı hatası: {res_df}")
                elif res_df.empty:
                    st.warning("Sitede henüz sonuç bulunamadı. Yarış başlamış olabilir, biraz bekleyin.")
                else:
                    df_temp = st.session_state.df.copy()
                    df_temp[live_gun] = df_temp[live_gun].astype(object)
                    updated = 0
                    for _, row in res_df.iterrows():
                        mask = df_temp['Göğüs No'] == row['Göğüs No']
                        if mask.any():
                            sure_val = str(row['Süre']).strip()
                            df_temp.loc[mask, live_gun] = sure_val
                            if sure_val.upper() in ['MP', 'DNF', 'DNS', 'DSQ']:
                                df_temp.loc[mask, 'Durum'] = sure_val.upper()
                            else:
                                df_temp.loc[mask, 'Durum'] = 'Tamamladı'
                            updated += 1
                    update_data(df_temp)
                    st.success(f"✅ {len(res_df)} sonuç çekildi, {updated} sporcu güncellendi!")
                    if updated < len(res_df):
                        st.info(f"ℹ️ {len(res_df) - updated} sporcu göğüs numarası eşleşmedi (farklı yarış kategorisi olabilir).")

        st.divider()
        st.subheader("3. Toplu Sonuç Yükleme (PDF)")
        col_res1, col_res2 = st.columns(2)
        with col_res1:
            res_file1 = st.file_uploader("1. Gün Sonuç PDF", type=['pdf'], key=f'res1_{st.session_state.key_res1}')
            if res_file1 and st.button("1. Gün Sonuçlarını İşle"):
                res_df = parse_results(res_file1)
                df_temp = st.session_state.df.copy()
                df_temp['1. Gün Süresi'] = df_temp['1. Gün Süresi'].astype(object)
                updated = 0
                for _, row in res_df.iterrows():
                    mask = df_temp['Göğüs No'] == row['Göğüs No']
                    if mask.any():
                        sure_val = row['Süre']
                        df_temp.loc[mask, '1. Gün Süresi'] = sure_val
                        if sure_val.upper() in ['MP', 'DNF', 'DNS', 'DSQ']:
                            df_temp.loc[mask, 'Durum'] = sure_val.upper()
                        else:
                            df_temp.loc[mask, 'Durum'] = 'Tamamladı'
                        updated += 1
                update_data(df_temp)
                st.session_state.key_res1 += 1
                st.success(f"1. Gün için {updated} sonuç başarıyla güncellendi!")
                st.rerun()
                
        with col_res2:
            res_file2 = st.file_uploader("2. Gün Sonuç PDF", type=['pdf'], key=f'res2_{st.session_state.key_res2}')
            if res_file2 and st.button("2. Gün Sonuçlarını İşle"):
                res_df = parse_results(res_file2)
                df_temp = st.session_state.df.copy()
                df_temp['2. Gün Süresi'] = df_temp['2. Gün Süresi'].astype(object)
                updated = 0
                for _, row in res_df.iterrows():
                    mask = df_temp['Göğüs No'] == row['Göğüs No']
                    if mask.any():
                        sure_val = row['Süre']
                        df_temp.loc[mask, '2. Gün Süresi'] = sure_val
                        if sure_val.upper() in ['MP', 'DNF', 'DNS', 'DSQ']:
                            df_temp.loc[mask, 'Durum'] = sure_val.upper()
                        else:
                            df_temp.loc[mask, 'Durum'] = 'Tamamladı'
                        updated += 1
                update_data(df_temp)
                st.session_state.key_res2 += 1
                st.success(f"2. Gün için {updated} sonuç başarıyla güncellendi!")
                st.rerun()

        st.divider()
        st.subheader("3. Hızlı Manuel Giriş")
        with st.form("manuel_giris"):
            m_gogus = st.text_input("Göğüs No")
            m_gun = st.selectbox("Gün", ["1. Gün Süresi", "2. Gün Süresi"])
            m_sure = st.text_input("Süre (MM:SS, HH:MM:SS veya MP/DNF/DNS)")
            submit = st.form_submit_button("Güncelle")
            if submit:
                df_temp = st.session_state.df.copy()
                df_temp[m_gun] = df_temp[m_gun].astype(object)
                if m_gogus in df_temp['Göğüs No'].values:
                    sure_val = m_sure.strip().upper()
                    df_temp.loc[df_temp['Göğüs No'] == m_gogus, m_gun] = sure_val
                    if sure_val in ['MP', 'DNF', 'DNS', 'DSQ']:
                        df_temp.loc[df_temp['Göğüs No'] == m_gogus, 'Durum'] = sure_val
                    else:
                        df_temp.loc[df_temp['Göğüs No'] == m_gogus, 'Durum'] = 'Tamamladı'
                    update_data(df_temp)
                    st.success(f"Göğüs No {m_gogus} başarıyla güncellendi!")
                else:
                    st.error("Göğüs No bulunamadı.")
                    
        st.divider()
        st.subheader("Tehlikeli İşlemler")
        if st.button("Tüm Veriyi Sıfırla"):
            update_data(pd.DataFrame(columns=['Göğüs No', 'İsim', 'Üniversite', 'Kategori', '1. Gün Çıkış', '2. Gün Çıkış', '1. Gün Süresi', '2. Gün Süresi', 'Durum']))
            st.warning("Veriler sıfırlandı!")
            
    elif admin_pass != "":
        st.error("Hatalı Şifre!")

