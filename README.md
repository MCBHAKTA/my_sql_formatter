# SQL formatter

Streamlit app that formats SQL in the PropSol house style used in the EPC notebooks.

## Run

```powershell
python -m pip install -r requirements.txt
python -m streamlit run "my_sql_formater.py"
```

Then open http://localhost:8501

The formatter only needs `streamlit` and `sqlparse`. The rest of `requirements.txt` is for the other scripts and notebooks in this backup folder.

## Usage

Paste SQL, or a Python cell with triple-quoted SQL strings. The sidebar **Input type** can be:

- **auto** — treat the input as Python if it looks like a notebook cell, otherwise as SQL
- **sql** — format the whole input as SQL
- **python** — format SQL inside `"""` / `'''` strings and leave the rest of the cell alone

**Load sample** fills a compacted version of the EPC solar `CREATE TABLE` query. **Wrap CASE at** controls when a long `WHEN … THEN` splits onto two lines.

## House style

- Uppercase keywords; identifiers keep the case you typed
- Leading commas with no space after the comma
- Right-aligned `FROM` / `WHERE` / `AND`
- Stacked `CASE WHEN` / `ELSE` / `END AS`
- `---- SECTION` banner comments kept in the select list
- CTEs as `WITH name AS (` then `SELECT` at column 0
- Simple `FROM (SELECT … FROM …)` subqueries stay on one line

Example:

```sql
SELECT col1
      ,col2
  FROM t
  LEFT JOIN u
    ON t.id = u.id
 WHERE x = 1
   AND y = 2
```

## Files

| File | Role |
|------|------|
| `my_sql_formater.py` | Streamlit UI |
| `sql_house_style.py` | Formatter |
| `requirements.txt` | Python packages for this backup folder |
