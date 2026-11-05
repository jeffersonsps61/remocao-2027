"""
Dashboard de apoio à Remoção 2027 - Professores Fase 04
Rodar:  streamlit run app.py
"""
import io
import json
import math
import pandas as pd
import streamlit as st
import plotly.express as px
import pydeck as pdk

st.set_page_config(page_title="Remoção 2027 - Minha lista de escolas", page_icon="🏫", layout="wide")

# ----------------------------------------------------------------- metadados das categorias
META = {  # sigla: (descrição, área, turno)
    "40I": ("Integral 40h", "Integral 40h", "Integral"),
    "30MEF": ("Manhã 30h Ed. Física", "Ed. Física", "Manhã"),
    "30TEF": ("Tarde 30h Ed. Física", "Ed. Física", "Tarde"),
    "24NEF": ("Noite 24h Ed. Física", "Ed. Física", "Noite"),
    "30MINF": ("Manhã 30h Infantil", "Infantil", "Manhã"),
    "30TINF": ("Tarde 30h Infantil", "Infantil", "Tarde"),
    "30MFUND": ("Manhã 30h Fundamental", "Fundamental", "Manhã"),
    "30TFUND": ("Tarde 30h Fundamental", "Fundamental", "Tarde"),
    "30MTEF": ("Manhã/Tarde 30h Ed. Física", "Ed. Física", "Manhã/Tarde"),
    "30MA": ("Manhã 30h Arte", "Arte", "Manhã"),
    "30TA": ("Tarde 30h Arte", "Arte", "Tarde"),
    "30MTA": ("Manhã/Tarde 30h Arte", "Arte", "Manhã/Tarde"),
    "30MI": ("Manhã 30h Inglês", "Inglês", "Manhã"),
    "30TI": ("Tarde 30h Inglês", "Inglês", "Tarde"),
    "30MTI": ("Manhã/Tarde 30h Inglês", "Inglês", "Manhã/Tarde"),
    "24NI": ("Noite 24h Inglês", "Inglês", "Noite"),
    "40TIEM": ("Tarde 40h Infantil - Tempo Integral", "Infantil", "Tarde"),
    "40TFEM": ("Tarde 40h Fundamental - Tempo Integral", "Fundamental", "Tarde"),
    "40MTEF": ("Manhã/Tarde 40h Ed. Física", "Ed. Física", "Manhã/Tarde"),
    "40MTA": ("Manhã/Tarde 40h Arte", "Arte", "Manhã/Tarde"),
    "40MTI": ("Manhã/Tarde 40h Inglês", "Inglês", "Manhã/Tarde"),
}
SIGLAS = list(META.keys())
MODOS = {
    "🚗 Carro (6h30, com trânsito)": "min_carro",
    "🚌 Transporte público (chegar 7h)": "min_transp",
    "🚲 Bicicleta": "min_bike",
    "🚶 A pé": "min_a_pe",
}

# ----------------------------------------------------------------- dados
@st.cache_data
def carregar():
    try:
        df = pd.read_csv("escolas_enriquecidas.csv")
        geo = True
    except FileNotFoundError:
        df = pd.read_csv("vagas_largo.csv")
        for c in ["endereco", "lat", "lon", "km_carro", "min_carro", "min_transp", "min_a_pe", "min_bike"]:
            df[c] = None
        geo = False
    df["nome"] = df["escola"].str.replace(r"\s*-\s*EMEB$", "", regex=True)
    return df, geo

df, tem_geo = carregar()
if not tem_geo:
    st.warning("Arquivo `escolas_enriquecidas.csv` não encontrado: mostrando só as vagas, sem distância/tempo. "
               "Rode `python enriquecer_enderecos.py` primeiro.")

@st.cache_data
def carregar_rotas():
    try:
        with open("rotas.json", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def decodificar_polyline(txt):
    """Decodifica polyline do Google -> lista de [lon, lat]."""
    pts, i, lat, lon = [], 0, 0, 0
    while i < len(txt):
        for eixo in (0, 1):
            res, shift = 0, 0
            while True:
                b = ord(txt[i]) - 63; i += 1
                res |= (b & 0x1F) << shift; shift += 5
                if b < 0x20: break
            delta = ~(res >> 1) if res & 1 else res >> 1
            if eixo == 0: lat += delta
            else: lon += delta
        pts.append([lon / 1e5, lat / 1e5])
    return pts

ROTAS = carregar_rotas()
CHAVE_ROTA = {"min_carro": "carro", "min_transp": "transp", "min_bike": "bike", "min_a_pe": "a_pe"}

if "fav" not in st.session_state:
    st.session_state.fav = set()
if "prio" not in st.session_state:
    st.session_state.prio = {}
    st.session_state.obs = {}

# ----------------------------------------------------------------- sidebar
st.sidebar.header("🎯 Filtros")
areas = st.sidebar.multiselect("Minha área / tipo de vaga", sorted({m[1] for m in META.values()}),
                               help="Deixe vazio para considerar todas.")
turnos = st.sidebar.multiselect("Turno", ["Manhã", "Tarde", "Manhã/Tarde", "Noite", "Integral"])
sel = [s for s in SIGLAS if (not areas or META[s][1] in areas) and (not turnos or META[s][2] in turnos)]
with st.sidebar.expander(f"Categorias consideradas ({len(sel)})"):
    for s in sel:
        st.caption(f"**{s}** – {META[s][0]}")

modos_ok = {n: c for n, c in MODOS.items() if c in df and df[c].notna().any()} or MODOS
modo_nome = st.sidebar.radio("Como vou me deslocar?", list(modos_ok.keys()))
col_tempo = modos_ok[modo_nome]
if col_tempo == "min_carro":
    fator = st.sidebar.slider("Fator de trânsito (carro)", 1.0, 2.0, 1.0, 0.1,
                              help="Tempos sem dados de trânsito ficam otimistas. Use 1,3–1.5 para horário de pico. "
                                   "Se o tempo já inclui trânsito (Google), deixe em 1,0.")
    df = df.assign(min_carro=(df.min_carro * fator).round())
tempo_max = st.sidebar.slider("Tempo máximo de deslocamento (min)", 10, 180, 90, step=5)
min_vagas = st.sidebar.slider("Mínimo de vagas relevantes", 1, 20, 1)

st.sidebar.subheader("⚖️ O que pesa mais?")
peso_tempo = st.sidebar.slider("Proximidade", 0, 10, 7)
peso_vagas = st.sidebar.slider("Quantidade de vagas (chance)", 0, 10, 3)

# ----------------------------------------------------------------- cálculos
d = df.copy()
d["vagas_rel"] = d[sel].sum(axis=1) if sel else 0
d["tempo"] = d[col_tempo]
d = d[d.vagas_rel >= min_vagas]
if tem_geo:
    d = d[d.tempo.isna() | (d.tempo <= tempo_max)]
    sem_rota = d.tempo.isna().sum()
else:
    sem_rota = 0

if len(d):
    t = d.tempo.fillna(d.tempo.max() if d.tempo.notna().any() else 0)
    tn = 1 - (t - t.min()) / ((t.max() - t.min()) or 1)
    vn = d.vagas_rel / d.vagas_rel.max()
    soma = (peso_tempo + peso_vagas) or 1
    d["score"] = ((peso_tempo * tn + peso_vagas * vn) / soma * 100).round(0)
    d["categorias"] = d.apply(
        lambda r: " · ".join(f"{s}×{int(r[s])}" for s in sel if r[s] > 0), axis=1)
    d = d.sort_values("score", ascending=False).reset_index(drop=True)
    d["rota_onibus"] = d.apply(lambda r: (
        f"https://www.google.com/maps/dir/?api=1&origin={r.origem_lat},{r.origem_lon}"
        f"&destination={r.lat},{r.lon}&travelmode=transit" if tem_geo and pd.notna(r.lat) else None), axis=1)
    d["rota"] = d.apply(lambda r: (
        f"https://www.google.com/maps/dir/?api=1&origin={r.origem_lat},{r.origem_lon}"
        f"&destination={r.lat},{r.lon}&travelmode=driving&avoid=tolls" if tem_geo and pd.notna(r.lat) else None), axis=1)

# ----------------------------------------------------------------- cabeçalho
st.title("🏫 Remoção 2027 – Minha lista de escolas")
if tem_geo:
    st.caption(f"📍 Partindo de: {df.origem_endereco.iloc[0]}")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Escolas no filtro", len(d))
c2.metric("Vagas relevantes", int(d.vagas_rel.sum()) if len(d) else 0)
c3.metric("Mais próxima", f"{int(d.tempo.min())} min" if len(d) and d.tempo.notna().any() else "–")
c4.metric("Na minha lista ⭐", len(st.session_state.fav))
if sem_rota:
    st.info(f"{sem_rota} escola(s) sem rota calculada (endereço a conferir) aparecem no fim do ranking.")

tab_rank, tab_mapa, tab_graf, tab_lista = st.tabs(["📋 Ranking", "🗺️ Mapa", "📊 Gráficos", "⭐ Minha lista"])

# ----------------------------------------------------------------- ranking
with tab_rank:
    if d.empty:
        st.warning("Nenhuma escola atende aos filtros. Relaxe o tempo máximo ou o mínimo de vagas.")
    else:
        busca = st.text_input("🔎 Buscar escola ou bairro/endereço")
        v = d if not busca else d[d.nome.str.contains(busca, case=False, na=False) |
                                  d.endereco.fillna("").str.contains(busca, case=False)]
        v = v.copy()
        v.insert(0, "⭐", v.escola.isin(st.session_state.fav))
        if "pedagio_carro" in v:
            v["pedagio"] = v.pedagio_carro.map({True: "⚠️ sim", False: "não"}).fillna("–")
        else:
            v["pedagio"] = "–"
        cols = ["⭐", "nome", "score", "vagas_rel", "categorias", "tempo", "km_carro", "pedagio", "endereco", "rota", "rota_onibus"]
        cfg = {
            "⭐": st.column_config.CheckboxColumn("⭐", help="Marque para levar à Minha lista"),
            "nome": "Escola",
            "score": st.column_config.ProgressColumn("Pontuação", min_value=0, max_value=100, format="%d"),
            "vagas_rel": st.column_config.NumberColumn("Vagas"),
            "categorias": "Tipos de vaga",
            "tempo": st.column_config.NumberColumn("Tempo (min)"),
            "km_carro": st.column_config.NumberColumn("Distância (km)", format="%.1f"),
            "endereco": "Endereço",
            "pedagio": "Pedágio (carro)",
            "rota": st.column_config.LinkColumn("Rota (carro)", display_text="abrir no Maps"),
            "rota_onibus": st.column_config.LinkColumn("Rota (ônibus)", display_text="ver ônibus"),
        }
        ed = st.data_editor(v[cols], column_config=cfg, hide_index=True, use_container_width=True,
                            disabled=[c for c in cols if c != "⭐"], height=520, key="editor_rank")
        for esc, marcado in zip(v.escola, ed["⭐"]):
            (st.session_state.fav.add if marcado else st.session_state.fav.discard)(esc)

# ----------------------------------------------------------------- mapa
with tab_mapa:
    if not tem_geo or d.empty or d.lat.isna().all():
        st.info("Mapa disponível depois de rodar o script de enriquecimento.")
    else:
        m = d.dropna(subset=["lat", "lon"]).copy()
        modo_rota = CHAVE_ROTA[col_tempo]
        cA, cB = st.columns([3, 2])
        escolha = cA.selectbox("🛣️ Ver trajeto até:", ["(nenhuma)"] + m.nome.tolist())
        todas_fav = cB.checkbox("Mostrar trajetos de toda a minha lista ⭐", value=False)
        if not ROTAS:
            st.warning("`rotas.json` não encontrado: rode o script de enriquecimento para ver os trajetos.")

        # trajetos a desenhar
        alvo = []
        if escolha != "(nenhuma)":
            alvo += m.loc[m.nome == escolha, "escola"].tolist()
        if todas_fav:
            alvo += [e for e in st.session_state.fav if e not in alvo]
        paths = []
        for esc in alvo:
            enc = ROTAS.get(esc, {}).get(modo_rota)
            if enc:
                nome = df.loc[df.escola == esc, "nome"].iloc[0]
                paths.append({"nome": nome, "path": decodificar_polyline(enc),
                              "cor": [220, 40, 40] if esc == (m.loc[m.nome == escolha, "escola"].iloc[0] if escolha != "(nenhuma)" else None) else [30, 90, 220]})

        # resumo da escola escolhida
        if escolha != "(nenhuma)":
            r = m[m.nome == escolha].iloc[0]
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Tempo", f"{int(r.tempo)} min" if pd.notna(r.tempo) else "–")
            k2.metric("Distância (carro)", f"{r.km_carro:.1f} km" if pd.notna(r.km_carro) else "–")
            k3.metric("Vagas relevantes", int(r.vagas_rel))
            k4.link_button("Abrir no Google Maps", r.rota) if r.rota else None
            if modo_rota == "carro":
                if r.get("pedagio_carro") is True:
                    st.error("⚠️ O Google não encontrou rota de carro sem pedágio até esta escola. Confira antes de decidir.")
                else:
                    st.success("✅ Rota de carro calculada **evitando pedágios**.")
            st.caption(r.endereco)

        tmax = m.tempo.max() or 1
        def cor(x):
            if pd.isna(x): return [150, 150, 150, 200]
            f = min(x / tmax, 1)
            return [int(40 + 215 * f), int(180 - 130 * f), 60, 210]
        m["cor"] = m.tempo.apply(cor)
        m["raio"] = 120 + 40 * m.vagas_rel
        m["tempo_txt"] = m.tempo.apply(lambda x: "–" if pd.isna(x) else f"{int(x)} min")
        m["fav"] = m.escola.isin(st.session_state.fav).map({True: "⭐ ", False: ""})
        casa = pd.DataFrame([{"lat": df.origem_lat.iloc[0], "lon": df.origem_lon.iloc[0]}])

        # enquadramento: se há trajeto, centraliza nele
        if paths:
            pts = [p for x in paths for p in x["path"]]
            lons, lats = [p[0] for p in pts], [p[1] for p in pts]
            centro = ((max(lats) + min(lats)) / 2, (max(lons) + min(lons)) / 2)
            amp = max(max(lats) - min(lats), max(lons) - min(lons), 0.01)
            zoom = max(8.5, min(13.5, 8.6 - math.log2(amp / 0.5) + 0.0))
        else:
            centro, zoom = (m.lat.mean(), m.lon.mean()), 10.3

        camadas = []
        if paths:
            camadas.append(pdk.Layer("PathLayer", paths, get_path="path", get_color="cor",
                                     width_min_pixels=5, pickable=True))
        camadas += [
            pdk.Layer("ScatterplotLayer", m, get_position="[lon, lat]", get_fill_color="cor",
                      get_radius="raio", pickable=True),
            pdk.Layer("ScatterplotLayer", casa, get_position="[lon, lat]", get_fill_color=[30, 60, 220, 255],
                      get_radius=350, stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=3),
        ]
        deck = pdk.Deck(
            map_style="light",
            initial_view_state=pdk.ViewState(latitude=centro[0], longitude=centro[1], zoom=zoom),
            layers=camadas,
            tooltip={"html": "<b>{fav}{nome}</b><br/>{endereco}<br/>Vagas: {vagas_rel} – {categorias}<br/>"
                             "Tempo: {tempo_txt} · {km_carro} km"},
        )
        st.pydeck_chart(deck, use_container_width=True)
        st.caption(f"🔵 sua casa · círculo maior = mais vagas · verde = perto, vermelho = longe · "
                   f"linha = trajeto no modo escolhido ({modo_nome}). Rotas de carro evitam pedágios.")

# ----------------------------------------------------------------- gráficos
with tab_graf:
    if d.empty:
        st.write("Sem dados para os filtros atuais.")
    else:
        g1, g2 = st.columns(2)
        if tem_geo and d.tempo.notna().any():
            fig = px.scatter(d, x="tempo", y="vagas_rel", size="vagas_rel", hover_name="nome", color="score",
                             color_continuous_scale="RdYlGn", labels={"tempo": "Tempo (min)", "vagas_rel": "Vagas", "score": "Pontuação"},
                             title="Perto × muitas vagas (canto superior esquerdo é o ideal)")
            g1.plotly_chart(fig, use_container_width=True)
        top = d.nlargest(15, "vagas_rel")
        g2.plotly_chart(px.bar(top.sort_values("vagas_rel"), x="vagas_rel", y="nome", orientation="h",
                               labels={"vagas_rel": "Vagas", "nome": ""}, title="15 escolas com mais vagas"),
                        use_container_width=True)
        tot = pd.DataFrame({"sigla": sel, "vagas": [int(df[s].sum()) for s in sel]})
        tot = tot[tot.vagas > 0]
        tot["descricao"] = tot.sigla.map(lambda s: META[s][0])
        st.plotly_chart(px.bar(tot, x="descricao", y="vagas", title="Vagas totais por categoria (toda a rede)",
                               labels={"descricao": "", "vagas": "Vagas"}), use_container_width=True)

# ----------------------------------------------------------------- minha lista
with tab_lista:
    fav = [e for e in df.escola if e in st.session_state.fav]
    if not fav:
        st.info("Marque ⭐ nas escolas do Ranking para montar sua lista de prioridades.")
    else:
        base = df[df.escola.isin(fav)].copy()
        base["vagas_rel"] = base[sel].sum(axis=1) if sel else 0
        base["tempo"] = base[col_tempo]
        base["Prioridade"] = base.escola.map(lambda e: st.session_state.prio.get(e, 999))
        base["Observações"] = base.escola.map(lambda e: st.session_state.obs.get(e, ""))
        base = base.sort_values(["Prioridade", "tempo"]).reset_index(drop=True)
        base["Prioridade"] = [p if p != 999 else None for p in base["Prioridade"]]
        st.write("Edite a coluna **Prioridade** (1 = primeira opção) e as **Observações**.")
        cols = ["Prioridade", "nome", "vagas_rel", "tempo", "km_carro", "endereco", "Observações"]
        ed = st.data_editor(
            base[cols], hide_index=True, use_container_width=True, key="editor_lista",
            disabled=["nome", "vagas_rel", "tempo", "km_carro", "endereco"],
            column_config={"Prioridade": st.column_config.NumberColumn(min_value=1, step=1),
                           "nome": "Escola", "vagas_rel": "Vagas", "tempo": "Tempo (min)",
                           "km_carro": st.column_config.NumberColumn("Km", format="%.1f"), "endereco": "Endereço"})
        for esc, p, o in zip(base.escola, ed["Prioridade"], ed["Observações"]):
            if pd.notna(p): st.session_state.prio[esc] = int(p)
            else: st.session_state.prio.pop(esc, None)
            st.session_state.obs[esc] = o or ""
        saida = ed.sort_values("Prioridade", na_position="last")
        buf = io.StringIO(); saida.to_csv(buf, index=False)
        st.download_button("⬇️ Baixar minha lista (CSV)", buf.getvalue().encode("utf-8-sig"),
                           "minha_lista_remocao.csv", "text/csv")
        if st.button("Limpar lista"):
            st.session_state.fav.clear(); st.session_state.prio.clear(); st.session_state.obs.clear()
            st.rerun()

st.caption("Fonte: Relatório de Grade de Vagas em Processo de Remoção – Vagas Reais (SE-311, 04/11/2026). "
           "Tempos estimados pelo Google Maps; confira a rota real antes de decidir.")
