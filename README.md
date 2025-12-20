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

# How does it look like

[demo video](https://github.com/user-attachments/assets/d32b5e4a-a537-4ca3-b911-c6e3967e2c6b)


