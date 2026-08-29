"""
J-League Data Viewer - ルーター(エントリポイント)

st.navigationでページを束ねる。各ページ(pages/*.py)は
それぞれ独立したSidebarを持つため、ページごとに必要な設定項目だけが表示される。
"""

import streamlit as st

st.set_page_config(page_title="J-League Data Viewer", layout="wide")

team_basis_page = st.Page(
    "pages/team_basis.py", title="Team Basis", icon="⚽", default=True
)
schedule_basis_page = st.Page(
    "pages/schedule_basis.py", title="Schedule Basis", icon="📅"
)

pg = st.navigation([team_basis_page, schedule_basis_page])
pg.run()
