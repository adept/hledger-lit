# hledger-lit

A python3 streamlit+plotly app to plot `hledger balance` reports:

- multi-line graph of assets/liabilities/income/expenses over time

- treemap graph of all expenses

- sankey graph of income vs expense money flows

- sankey graph of money flows between income, expenses, assets and liabilities account categories

# Installation & usage

```
python3 -m venv .venv
source ./.venv/bin/activate
pip install -r requirements.txt
streamlit run hledger_lit.py
```

This should open the app page in your browser. Defaults should be sensible enough for you to press "Generate Visualizations" and see the graphs immediately.

# Try it

Repository contains `example.journal` generated out of slighly edited `Cody.journal` from hledger examples. Set the time range to 2021-01-01 to 2021-12-31.

# How would it look like
[demo video](https://github.com/user-attachments/assets/c08b2bc1-5eda-48f0-a132-1956f9ba323e)

