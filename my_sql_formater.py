# python -m streamlit run "C:\Users\M42676\Python Scripts\propsol_backup_20260427_173146\my_sql_formater.py"

import streamlit as st

from sql_house_style import format_text

SAMPLE_SQL = """
create or replace table ${v_e_propsol_uk_db}.PROPSOL_UK.EPC_SOLAR as
with solar_improvements as (
select lmk_key, array_to_string(array_agg(distinct description) within group (order by description), ',') as suggestions, max(avg_indicative_cost) indicative_cost_gbp
from (select * from ${v_e_propsol_uk_db}.PROPSOL_UK.EPC_RECOMMENDATIONS_CORTEX_SUMMERISED where raw_output = 'SOLAR')
group by lmk_key)
, other_solar_attr as (
select lmk_key, case when solar_water_heating_flag = 'Y' then 'Y' else null end as solar_water_heating_flag, nvl(photo_supply, 0) as photo_supply
from ${v_e_propsol_uk_db}.PROPSOL_UK.EPC_ATTR x)
select lmk_key, solar_water_heating_flag, photo_supply
from (select x.lmk_key, case when length(solar_water_heating_flag) != 0 then solar_water_heating_flag else null end as solar_water_heating_flag
from (select lmk_key from ${v_e_propsol_uk_db}.PROPSOL_UK.EPC_ATTR) x
left join solar_improvements a on x.lmk_key = a.lmk_key
left join other_solar_attr b on x.lmk_key = b.lmk_key);
""".strip()

st.set_page_config(
    page_title="SQL formatter",
    page_icon=":material/database:",
    layout="wide",
)

st.title("SQL formatter")
st.caption(
    "Formats SQL in your house style: uppercase keywords, leading commas, "
    "right-aligned FROM/WHERE, stacked CASE, and `---- SECTION` banners. "
    "Paste SQL, or a Python cell with triple-quoted SQL strings."
)


def load_sample() -> None:
    st.session_state.sql_input = SAMPLE_SQL


def clear_input() -> None:
    st.session_state.sql_input = ""


with st.sidebar:
    st.header("Options")
    input_kind = st.segmented_control(
        "Input type",
        options=["auto", "sql", "python"],
        default="auto",
        required=True,
        help="Python mode formats SQL inside triple-quoted strings and leaves the rest of the cell alone.",
    )
    wrap_width = st.slider(
        "Wrap CASE at",
        min_value=80,
        max_value=160,
        value=110,
        help="WHEN/THEN stays on one line unless the line would exceed this width.",
    )

with st.container(horizontal=True):
    st.button("Load sample", icon=":material/data_object:", on_click=load_sample)
    st.button("Clear", icon=":material/delete:", on_click=clear_input)

input_col, output_col = st.columns(2, gap="large")

with input_col:
    sql_input = st.text_area(
        "Input",
        height=520,
        placeholder="Paste SQL or a Python cell…",
        key="sql_input",
    )

python_strings = None
if input_kind == "sql":
    python_strings = False
elif input_kind == "python":
    python_strings = True

with output_col:
    st.markdown("Formatted")
    if not (sql_input or "").strip():
        st.info("Paste SQL on the left to see formatted output.")
    else:
        try:
            formatted = format_text(
                sql_input,
                wrap_width=wrap_width,
                python_strings=python_strings,
            )
        except Exception as exc:
            st.error(f"Could not format SQL: {exc}")
        else:
            language = "python" if python_strings or (python_strings is None and "\"\"\"" in sql_input) else "sql"
            st.code(formatted, language=language, line_numbers=True, wrap_lines=True, height=520)
            with st.container(horizontal=True):
                st.download_button(
                    "Download",
                    data=formatted.encode("utf-8"),
                    file_name="formatted.py" if language == "python" else "formatted.sql",
                    mime="text/plain",
                    icon=":material/download:",
                )
                st.caption(f"{len(formatted.splitlines())} lines · {len(formatted):,} characters")
