import json
import os
import subprocess
from datetime import datetime, date
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pprint import pformat

# Account name substrings for recognising account types
ASSET_ACCOUNT_PAT     = 'assets'
LIABILITY_ACCOUNT_PAT = 'liabilities'
INCOME_ACCOUNT_PAT    = 'income'
EXPENSE_ACCOUNT_PAT   = 'expenses'
OTHER_TOPLEVEL = ['revenues','virtual']

# Toplevel account categories that you have in your chart of accounts.
# Used to filter out non-account entries from the JSON balance report
TOPLEVEL_ACCOUNT_CATEGORIES=[INCOME_ACCOUNT_PAT,EXPENSE_ACCOUNT_PAT,ASSET_ACCOUNT_PAT,LIABILITY_ACCOUNT_PAT] + OTHER_TOPLEVEL

HLEDGER_EXTRA_ARGS = ''


# assets:cash -> assets
# assets -> ''
def parent(account_name):
    return ':'.join(account_name.split(':')[:-1])

def run_hledger_command(command):
    """Execute hledger command and return parsed JSON output."""
    process_output = subprocess.run(command.split(' '), stdout=subprocess.PIPE, text=True).stdout
    return json.loads(process_output)

def read_current_balances(filename, account_categories, commodity, start_date=None, end_date=None):
    # You might want to try just "income expenses" as account categories, or less depth via "--depth 2"
    # Explanation for the choice of arguments:
    # "balance income expenses assets liabilities" are account categories
    # "not:desc:opening" excludes year-open transaction which carries over values of assets from the previous year, as we are only interested in asset increases, not
    #     absolute value
    # "--cost --value=then,<commodity> --infer-value" - convert everything to a single commodity
    # "--no-total" - ensure that we dont have a total row
    # "--tree --no-elide" - ensure that parent accounts are listed even if they dont have balance changes, to make sure that our sankey flows dont have gaps
    # "-O json" to produce JSON output
    command = 'hledger -f %s balance %s not:desc:opening --cost --value=then,%s --infer-value --no-total --tree --no-elide -O json' % (filename, account_categories, commodity)

    # Add date range if provided
    if start_date:
        command += f' -b {start_date}'
    if end_date:
        command += f' -e {end_date}'

    command += ' ' + HLEDGER_EXTRA_ARGS

    # Execute command and parse JSON output
    data = run_hledger_command(command)

    # First element of the JSON array contains the account entries
    accounts = data[0]

    # Build list of (account_name, balance) tuples
    balances = []
    for entry in accounts:
        account_name = entry[0]
        # Filter to only include accounts that match our top-level categories
        if any(cat in account_name for cat in TOPLEVEL_ACCOUNT_CATEGORIES):
            # Get the balance from the amounts array (entry[3])
            amounts = entry[3]
            if amounts:
                balance = amounts[0]["aquantity"]["floatingPoint"]
            else:
                balance = 0
            balances.append((account_name, balance))

    return balances

def read_historical_balances(filename, commodity, start_date=None, end_date=None,
                            income_pattern=INCOME_ACCOUNT_PAT, expense_pattern=EXPENSE_ACCOUNT_PAT,
                            asset_pattern=ASSET_ACCOUNT_PAT, liability_pattern=LIABILITY_ACCOUNT_PAT,
                            other_categories=None):
    """Read historical daily cumulative balances for all top-level account categories."""
    # Build list of account categories including user-provided patterns
    if other_categories is None:
        other_categories = []
    toplevel_categories = [income_pattern, expense_pattern, asset_pattern, liability_pattern] + other_categories
    account_categories = ' '.join(toplevel_categories)
    command = f'hledger -f {filename} balance {account_categories} not:tag:clopen --depth 1 --period daily --historical --value=then,{commodity} --infer-value -O json'

    # Add date range if provided
    if start_date:
        command += f' -b {start_date}'
    if end_date:
        command += f' -e {end_date}'

    command += ' ' + HLEDGER_EXTRA_ARGS

    # Execute command and parse JSON output
    data = run_hledger_command(command)

    # Extract dates from prDates - use the start date of each period
    dates = [period[0]['contents'] for period in data['prDates']]

    # Extract balances for each account
    balances = {}
    for row in data['prRows']:
        account_name = row['prrName']
        # Only include accounts that match our top-level categories
        if account_name in toplevel_categories:
            # Extract floating point values from each period and apply abs()
            account_balances = []
            for amount_list in row['prrAmounts']:
                balance = 0
                if amount_list:
                    # Find the amount matching the desired commodity
                    for amount in amount_list:
                        if amount['acommodity'] == commodity:
                            balance = abs(amount['aquantity']['floatingPoint'])
                            break
                account_balances.append(balance)
            balances[account_name] = account_balances

    # Calculate net worth as assets - liabilities
    if asset_pattern in balances and liability_pattern in balances:
        net_worth = [assets - liabilities
                     for assets, liabilities in zip(balances[asset_pattern], balances[liability_pattern])]
        balances['net_worth'] = net_worth
    elif asset_pattern in balances:
        balances['net_worth'] = balances[asset_pattern][:]

    return {'dates': dates, 'balances': balances}

# Convert hledger balance report into a list of (source, target, value) tuples for the sankey graph.
# We make the following assumptions:
# 1. Balance report will have top-level categories "assents","income","expenses","liabilities" with the usual semantics.
#    I also have "virtual:assets profit and loss" for unrealized P&L, which also matches this query.
# 2. For sankey diagram, we want to see how "income" is being used to cover "expenses", increas the value of "assets" and pay off "liabilities", so we assume that
#    by default the money are flowing from income to the other categores.
# 3. However, positive income or negative expenses/assets/liabilities would be correctly treated as money flowing against the "usual" direction
def to_sankey_data(balances, income_pattern=INCOME_ACCOUNT_PAT, expense_pattern=EXPENSE_ACCOUNT_PAT,
                   asset_pattern=ASSET_ACCOUNT_PAT, liability_pattern=LIABILITY_ACCOUNT_PAT,
                   other_categories=None):
    # List to store (source, target, value) tuples
    sankey_data = []

    # A set of all accounts mentioned in the report, to check that parent accounts have known balance
    accounts = set(account_name for account_name, _ in balances)

    # Build list of top-level categories for checking
    if other_categories is None:
        other_categories = []
    toplevel_categories = [income_pattern, expense_pattern, asset_pattern, liability_pattern] + other_categories

    # Convert report to sankey data
    for account_name, balance in balances:
        # top-level accounts need to be connected to the special "pot" intermediate bucket
        # We assume that "income" and "virtual" accounts contribute to pot, while expenses draw from it
        if account_name in toplevel_categories:
            parent_acc = 'pot'
        else:
            parent_acc = parent(account_name)
            if parent_acc not in accounts:
                raise Exception(f'for account {account_name}, parent account {parent_acc} not found - have you forgotten --no-elide?')

        # income and virtual flow 'up'
        if income_pattern in account_name or 'virtual' in account_name:
            # Negative income is just income, positive income is a reduction, pay-back or something similar
            # For sankey, all flow values should be positive
            if balance < 0:
                source, target = account_name, parent_acc
            else:
                source, target = parent_acc, account_name
        else:
            # positive expenses/assets are normal expenses or investements or purchase of assets, negative values are cashbacks, or cashing in of investments
            if balance >= 0:
                source, target = parent_acc, account_name
            else:
                source, target = account_name, parent_acc

        sankey_data.append((source, target, abs(balance)))

    return sankey_data

def sankey_plot(sankey_data):
    # Sort by (target, source) to keep related accounts close together in the initial layout
    sankey_data = sorted(sankey_data, key=lambda x: (x[1], x[0]))

    # Get unique node names
    nodes = list(dict.fromkeys(
        [source for source, _, _ in sankey_data] +
        [target for _, target, _ in sankey_data]
    ))

    # Create Sankey diagram
    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=25,
            thickness=20,
            line=dict(color="black", width=0.5),
            label=nodes,
            color="blue"
        ),
        link=dict(
            source=[nodes.index(source) for source, _, _ in sankey_data],
            target=[nodes.index(target) for _, target, _ in sankey_data],
            value=[value for _, _, value in sankey_data]
        ))])

    return fig

def expenses_treemap_plot(balances, expense_pattern=EXPENSE_ACCOUNT_PAT):
    # Filter to only expenses
    expenses = [(name, value) for name, value in balances if expense_pattern in name]

    labels = [name for name, _ in expenses]
    values = [value for _, value in expenses]
    parents = [parent(name) for name, _ in expenses]

    fig = go.Figure(go.Treemap(
        labels=labels,
        parents=parents,
        values=values,
        branchvalues='total'
    ))

    return fig

def historical_balances_plot(historical_data):
    """Create line chart showing historical balances for each account category plus net worth."""
    dates = historical_data['dates']
    balances = historical_data['balances']

    fig = go.Figure()

    # Add traces for each account category
    for account_name in sorted(balances.keys()):
        if account_name != 'net_worth':  # We'll add net worth separately at the end
            fig.add_trace(go.Scatter(
                x=dates,
                y=balances[account_name],
                mode='lines',
                name=account_name
            ))

    # Add net worth as a separate line with emphasis
    if 'net_worth' in balances:
        fig.add_trace(go.Scatter(
            x=dates,
            y=balances['net_worth'],
            mode='lines',
            name='net_worth',
            line=dict(width=3, dash='dash')
        ))

    fig.update_layout(
        title="Historical Account Balances",
        xaxis_title="Date",
        yaxis_title="Balance (log scale)",
        yaxis_type="log",
        hovermode='x unified'
    )

    return fig


# Streamlit App
st.set_page_config(page_title="HLedger Sankey Visualizer", layout="wide")

st.title("HLedger Cash Flow Visualizer")
st.markdown("Generate Sankey diagrams and treemaps from hledger balance reports")

# Sidebar for inputs
with st.sidebar:
    st.header("Configuration")

    filename = st.text_input(
        "HLedger Journal File Path",
        value=os.environ.get('LEDGER_FILE', ''),
        help="Path to your hledger journal file"
    )

    commodity = st.text_input(
        "Commodity",
        value="£",
        help="Commodity to convert all values to"
    )

    # Date range inputs
    current_year = date.today().year
    start_date = st.date_input(
        "Start Date",
        value=date(current_year, 1, 1),
        help="Beginning date for the report (hledger -b flag)"
    )

    end_date = st.date_input(
        "End Date",
        value=date.today(),
        help="End date for the report (hledger -e flag)"
    )

    generate_button = st.button("Generate Visualizations", type="primary")

    st.subheader("Account Patterns")
    st.caption("Customize account category patterns for matching")

    income_pattern = st.text_input(
        "Income Account Pattern",
        value=INCOME_ACCOUNT_PAT,
        help="Pattern to match income accounts"
    )

    expense_pattern = st.text_input(
        "Expense Account Pattern",
        value=EXPENSE_ACCOUNT_PAT,
        help="Pattern to match expense accounts"
    )

    asset_pattern = st.text_input(
        "Asset Account Pattern",
        value=ASSET_ACCOUNT_PAT,
        help="Pattern to match asset accounts"
    )

    liability_pattern = st.text_input(
        "Liability Account Pattern",
        value=LIABILITY_ACCOUNT_PAT,
        help="Pattern to match liability accounts"
    )

    other_categories = st.text_input(
        "Other Top-Level Categories",
        value="revenues, virtual",
        help="Comma-separated list of other top-level account categories"
    )

# Main content
if generate_button:
    if not filename:
        st.error("Please provide a path to your hledger journal file")
    else:
        try:
            with st.spinner("Generating visualizations..."):
                # Parse other categories (comma-separated list)
                other_cats = [cat.strip() for cat in other_categories.split(',') if cat.strip()]

                # Sankey graph for all balances/flows
                all_pat = income_pattern + ' ' + expense_pattern + ' ' + asset_pattern + ' ' + liability_pattern
                all_balances = read_current_balances(filename, all_pat, commodity, start_date, end_date)
                all_balances_sankey = to_sankey_data(all_balances, income_pattern, expense_pattern, asset_pattern, liability_pattern, other_cats)
                all_balances_fig = sankey_plot(all_balances_sankey)

                # Sankey graph for just income/expenses
                income_expenses_pat = income_pattern + ' ' + expense_pattern
                income_expenses = read_current_balances(filename, income_expenses_pat, commodity, start_date, end_date)
                income_expenses_sankey = to_sankey_data(income_expenses, income_pattern, expense_pattern, asset_pattern, liability_pattern, other_cats)
                income_expenses_fig = sankey_plot(income_expenses_sankey)

                # Expenses treemap plot for just expenses
                expenses_fig = expenses_treemap_plot(income_expenses, expense_pattern)

                # Historical balances plot
                historical_data = read_historical_balances(filename, commodity, start_date, end_date, income_pattern, expense_pattern, asset_pattern, liability_pattern, other_cats)
                historical_fig = historical_balances_plot(historical_data)

                # Display all graphs
                st.success("Visualizations generated successfully!")

                st.header("Historical Account Balances")
                st.plotly_chart(historical_fig, width='stretch')

                st.header("Expenses Treemap")
                st.plotly_chart(expenses_fig, width='stretch')

                st.header("Income & Expenses Flow")
                st.plotly_chart(income_expenses_fig, width='stretch')

                st.header("All Cash Flows")
                st.plotly_chart(all_balances_fig, width='stretch')

        except subprocess.CalledProcessError as e:
            st.error(f"Error running hledger command: {e}")
        except json.JSONDecodeError as e:
            st.error(f"Error parsing JSON output: {e}")
        except Exception as e:
            st.error(f"Error: {e}")
            st.exception(e)
else:
    st.info("👈 Configure your settings in the sidebar and click 'Generate Visualizations' to start")
