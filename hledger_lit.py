import json
import os
import re
import subprocess
from datetime import datetime, date
from pathlib import Path
import configparser
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pprint import pformat

# Config file handling
def get_config_path():
    """Get the path to the config file."""
    config_home = os.environ.get('XDG_CONFIG_HOME')
    if not config_home:
        config_home = os.path.join(os.path.expanduser('~'), '.config')
    config_dir = Path(config_home)
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / 'hledger-lit.conf'

def load_config():
    """Load configuration from config file."""
    config = configparser.ConfigParser()
    config_path = get_config_path()
    if config_path.exists():
        config.read(config_path)
    return config

def get_config_value(config, section, key, default):
    """Get a config value with fallback to default."""
    try:
        return config.get(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return default

def save_config(filename, commodity, income_regex, expense_regex, asset_regex, liability_regex):
    """Save current configuration to config file."""
    config = configparser.ConfigParser()

    # Create sections
    config['settings'] = {
        'filename': filename,
        'commodity': commodity
    }

    config['regex'] = {
        'income': income_regex,
        'expense': expense_regex,
        'asset': asset_regex,
        'liability': liability_regex,
    }

    # Write to file
    config_path = get_config_path()
    with open(config_path, 'w') as configfile:
        config.write(configfile)

def reset_config():
    """Delete the config file to reset to defaults."""
    config_path = get_config_path()
    if config_path.exists():
        config_path.unlink()

# Regular expressions for matching account types
ASSET_REGEX     = 'assets'
LIABILITY_REGEX = 'liabilities'
INCOME_REGEX    = 'income|virtual|revenues'
EXPENSE_REGEX   = 'expenses'

# Account regex patterns for your chart of accounts
# Used to filter out non-account entries from the JSON balance report
TOPLEVEL_ACCOUNT_CATEGORIES=[INCOME_REGEX,EXPENSE_REGEX,ASSET_REGEX,LIABILITY_REGEX]

HLEDGER_EXTRA_ARGS = ''


# assets:cash -> assets
# assets -> ''
def parent(account_name):
    return ':'.join(account_name.split(':')[:-1])

def run_hledger_command(command):
    """Execute hledger command and return parsed JSON output."""
    process_output = subprocess.run(command.split(' '), stdout=subprocess.PIPE, text=True).stdout
    return json.loads(process_output)

def read_current_balances(filename, account_regex, commodity, start_date=None, end_date=None):
    # Explanation for the choice of arguments:
    # "balance <account_regex>" - regex pattern to match accounts (e.g., "income|expenses|assets|liabilities")
    # "not:tag:clopen" excludes closing/opening transactions
    # "--cost --value=then,<commodity> --infer-value" - convert everything to a single commodity
    # "--no-total" - ensure that we dont have a total row
    # "--tree --no-elide" - ensure that parent accounts are listed even if they dont have balance changes, to make sure that our sankey flows dont have gaps
    # "-O json" to produce JSON output
    command = f'hledger -f {filename} balance {account_regex} not:tag:clopen --cost --value=then,{commodity} --infer-value --no-total --tree --no-elide -O json'

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

    # Convert space-separated patterns to regex OR pattern
    # e.g., "income expenses" becomes "income|expenses"
    regex_string = '|'.join(account_regex.split())
    regex_pattern = re.compile(regex_string)

    # Build list of (account_name, balance) tuples
    balances = []
    for entry in accounts:
        account_name = entry[0]
        # Filter to only include accounts that match the regex pattern
        if regex_pattern.search(account_name):
            # Get the balance from the amounts array (entry[3])
            amounts = entry[3]
            if amounts:
                balance = amounts[0]["aquantity"]["floatingPoint"]
            else:
                balance = 0
            balances.append((account_name, balance))

    return balances

def read_historical_balances(filename, commodity, start_date=None, end_date=None,
                            income_regex=INCOME_REGEX, expense_regex=EXPENSE_REGEX,
                            asset_regex=ASSET_REGEX, liability_regex=LIABILITY_REGEX):
    """Read historical daily cumulative balances for accounts matching regex patterns."""
    # Build list of account regex patterns
    account_categories = ' '.join([income_regex, expense_regex, asset_regex, liability_regex])
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
    num_periods = len(dates)

    # Compile regex patterns for matching
    account_patterns = re.compile('|'.join(account_categories.split()))
    asset_pattern = re.compile('|'.join(asset_regex.split()))
    liability_pattern = re.compile('|'.join(liability_regex.split()))

    # Initialize net worth tracking
    net_worth = [0.0] * num_periods

    # Extract balances for each account
    balances = {}
    for row in data['prRows']:
        account_name = row['prrName']
        # Only include accounts that match our regex patterns
        if account_patterns.search(account_name):
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

            # Update net worth: add assets, subtract liabilities
            if asset_pattern.search(account_name):
                net_worth = [nw + bal for nw, bal in zip(net_worth, account_balances)]
            elif liability_pattern.search(account_name):
                net_worth = [nw - bal for nw, bal in zip(net_worth, account_balances)]

    # Add net worth to balances
    balances['net_worth'] = net_worth

    return {'dates': dates, 'balances': balances}

# Convert hledger balance report into a list of (source, target, value) tuples for the sankey graph.
# We make the following assumptions:
# 1. Balance report will have top-level categories "assents","income","expenses","liabilities" with the usual semantics.
#    I also have "virtual:assets profit and loss" for unrealized P&L, which also matches this query.
# 2. For sankey diagram, we want to see how "income" is being used to cover "expenses", increas the value of "assets" and pay off "liabilities", so we assume that
#    by default the money are flowing from income to the other categores.
# 3. However, positive income or negative expenses/assets/liabilities would be correctly treated as money flowing against the "usual" direction
def to_sankey_data(balances, income_regex=INCOME_REGEX, expense_regex=EXPENSE_REGEX,
                   asset_regex=ASSET_REGEX, liability_regex=LIABILITY_REGEX):
    # List to store (source, target, value) tuples
    sankey_data = []

    # A set of all accounts mentioned in the report, to check that parent accounts have known balance
    accounts = set(account_name for account_name, _ in balances)

    # Compile regex pattern for income matching
    income_pattern = re.compile('|'.join(income_regex.split()))

    # Convert report to sankey data
    for account_name, balance in balances:
        # top-level accounts need to be connected to the special "pot" intermediate bucket
        # We assume that income accounts (including virtual, revenues) contribute to pot, while expenses draw from it
        if ':' not in account_name:
            parent_acc = 'pot'
        else:
            parent_acc = parent(account_name)
            if parent_acc not in accounts:
                raise Exception(f'for account {account_name}, parent account {parent_acc} not found - have you forgotten --no-elide?')

        # income accounts flow 'up'
        if income_pattern.search(account_name):
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

def expenses_treemap_plot(balances):
    # balances already contains only expenses
    labels = [name for name, _ in balances]
    values = [value for _, value in balances]
    parents = [parent(name) for name, _ in balances]

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
st.set_page_config(page_title="'HLedger is Lit!' Visualizer", layout="wide")

st.title("'HLedger is Lit!' Visualizer")
st.markdown("Generate graphs from hledger balance reports")

# Load configuration
config = load_config()

# Sidebar for inputs
with st.sidebar:
    st.header("Configuration")

    # Get default values from config with fallbacks
    default_filename = get_config_value(config, 'settings', 'filename', os.environ.get('LEDGER_FILE', ''))
    default_commodity = get_config_value(config, 'settings', 'commodity', '£')

    filename = st.text_input(
        "HLedger Journal File Path",
        value=default_filename,
        help="Path to your hledger journal file, defaults to $LEDGER_FILE"
    )

    commodity = st.text_input(
        "Commodity",
        value=default_commodity,
        help="Commodity to convert all values to (via -value=then,{commodity})"
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

    save_config_button = st.button("Save Config")

    reset_config_button = st.button("Reset to Defaults")

    st.subheader("Account Regex Patterns")
    st.caption("Regular expressions for matching account categories")

    # Get default values from config with fallbacks
    default_income = get_config_value(config, 'regex', 'income', INCOME_REGEX)
    default_expense = get_config_value(config, 'regex', 'expense', EXPENSE_REGEX)
    default_asset = get_config_value(config, 'regex', 'asset', ASSET_REGEX)
    default_liability = get_config_value(config, 'regex', 'liability', LIABILITY_REGEX)

    income_regex = st.text_input(
        "Income Regex",
        value=default_income,
        help="Regex to match income accounts (e.g., 'income'). Multiple patterns can be separated by space or '|'"
    )

    expense_regex = st.text_input(
        "Expense Regex",
        value=default_expense,
        help="Regex to match expense accounts (e.g., 'expenses'). Multiple patterns can be separated by space or '|'"
    )

    asset_regex = st.text_input(
        "Asset Regex",
        value=default_asset,
        help="Regex to match asset accounts (e.g., 'assets'). Multiple patterns can be separated by space or '|'"
    )

    liability_regex = st.text_input(
        "Liability Regex",
        value=default_liability,
        help="Regex to match liability accounts (e.g., 'liabilities'). Multiple patterns can be separated by space or '|'"
    )

# Handle Save Config button
if save_config_button:
    save_config(filename, commodity, income_regex, expense_regex, asset_regex, liability_regex)
    st.success(f"Configuration saved to {get_config_path()}")

# Handle Reset to Defaults button
if reset_config_button:
    reset_config()
    st.success("Configuration reset to defaults. Please refresh the page to see the changes.")

# Check if filename is provided
if not filename:
    st.warning("👈 Please provide a path to your hledger journal file in the sidebar")
    st.stop()

# Initialize session state for storing graphs
if 'historical_fig' not in st.session_state:
    st.session_state.historical_fig = None
if 'expenses_fig' not in st.session_state:
    st.session_state.expenses_fig = None
if 'income_expenses_fig' not in st.session_state:
    st.session_state.income_expenses_fig = None
if 'all_balances_fig' not in st.session_state:
    st.session_state.all_balances_fig = None

# Historical Account Balances
st.header("Historical Account Balances")
st.caption("💡 Tip: Click legend items to show/hide lines, double-click to isolate a single line")
generate_historical = st.button("Generate Historical Balances", key="gen_historical")

if generate_historical:
    try:
        with st.spinner("Generating historical balances..."):
            historical_data = read_historical_balances(filename, commodity, start_date, end_date, income_regex, expense_regex, asset_regex, liability_regex)
            st.session_state.historical_fig = historical_balances_plot(historical_data)
    except subprocess.CalledProcessError as e:
        st.error(f"Error running hledger command: {e}")
    except json.JSONDecodeError as e:
        st.error(f"Error parsing JSON output: {e}")
    except Exception as e:
        st.error(f"Error: {e}")
        st.exception(e)

if st.session_state.historical_fig is not None:
    st.plotly_chart(st.session_state.historical_fig, width='stretch')

st.divider()

# Expenses Treemap
st.header("Expenses Treemap")
generate_treemap = st.button("Generate Expenses Treemap", key="gen_treemap")

if generate_treemap:
    try:
        with st.spinner("Generating expenses treemap..."):
            expenses = read_current_balances(filename, expense_regex, commodity, start_date, end_date)
            st.session_state.expenses_fig = expenses_treemap_plot(expenses)
    except subprocess.CalledProcessError as e:
        st.error(f"Error running hledger command: {e}")
    except json.JSONDecodeError as e:
        st.error(f"Error parsing JSON output: {e}")
    except Exception as e:
        st.error(f"Error: {e}")
        st.exception(e)

if st.session_state.expenses_fig is not None:
    st.plotly_chart(st.session_state.expenses_fig, width='stretch')

st.divider()

# Income & Expenses Flows
st.header("Income & Expenses Flows")
generate_income_expenses = st.button("Generate Income & Expenses Flows", key="gen_income_expenses")

if generate_income_expenses:
    try:
        with st.spinner("Generating income & expenses flows..."):
            income_expenses_pat = income_regex + ' ' + expense_regex
            income_expenses = read_current_balances(filename, income_expenses_pat, commodity, start_date, end_date)
            income_expenses_sankey = to_sankey_data(income_expenses, income_regex, expense_regex, asset_regex, liability_regex)
            st.session_state.income_expenses_fig = sankey_plot(income_expenses_sankey)
    except subprocess.CalledProcessError as e:
        st.error(f"Error running hledger command: {e}")
    except json.JSONDecodeError as e:
        st.error(f"Error parsing JSON output: {e}")
    except Exception as e:
        st.error(f"Error: {e}")
        st.exception(e)

if st.session_state.income_expenses_fig is not None:
    st.plotly_chart(st.session_state.income_expenses_fig, width='stretch')

st.divider()

# All Cash Flows
st.header("All Cash Flows")
generate_all_flows = st.button("Generate All Cash Flows", key="gen_all_flows")

if generate_all_flows:
    try:
        with st.spinner("Generating all cash flows..."):
            all_pat = income_regex + ' ' + expense_regex + ' ' + asset_regex + ' ' + liability_regex
            all_balances = read_current_balances(filename, all_pat, commodity, start_date, end_date)
            all_balances_sankey = to_sankey_data(all_balances, income_regex, expense_regex, asset_regex, liability_regex)
            st.session_state.all_balances_fig = sankey_plot(all_balances_sankey)
    except subprocess.CalledProcessError as e:
        st.error(f"Error running hledger command: {e}")
    except json.JSONDecodeError as e:
        st.error(f"Error parsing JSON output: {e}")
    except Exception as e:
        st.error(f"Error: {e}")
        st.exception(e)

if st.session_state.all_balances_fig is not None:
    st.plotly_chart(st.session_state.all_balances_fig, width='stretch')
