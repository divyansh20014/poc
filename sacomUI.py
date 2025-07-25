from flask import Flask, request, render_template_string
import pandas as pd
import os
from datetime import datetime
import paramiko

app = Flask(__name__)

# Local path inside container
UPLOAD_FOLDER = '/hostdata'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Remote machine details
REMOTE_FOLDER = "/home/workstation2/fraud detection module/data"
REMOTE_HOST = "10.0.194.120"
REMOTE_USER = "workstation2"
REMOTE_PASS = "12345"

def upload_to_remote_server(local_path, remote_filename):
    try:
        remote_path = os.path.join(REMOTE_FOLDER, remote_filename)
        transport = paramiko.Transport((REMOTE_HOST, 22))
        transport.connect(username=REMOTE_USER, password=REMOTE_PASS)
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.put(local_path, remote_path)
        sftp.close()
        transport.close()
        print(f"? File '{remote_filename}' uploaded to remote server.")
        return True
    except Exception as e:
        print(f"? Upload failed for {remote_filename}: {e}")
        return False

def run_remote_batch_script():
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(REMOTE_HOST, username=REMOTE_USER, password=REMOTE_PASS)

        command = 'cd "/home/workstation2/fraud detection module" && python3 scripts/run_batch.py'
        stdin, stdout, stderr = ssh.exec_command(command)

        output = stdout.read().decode()
        error = stderr.read().decode()

        ssh.close()

        if error:
            return f"? Remote batch script failed:<br>{error}"
        else:
            return f"? Remote batch script executed successfully:<br>{output}"
    except Exception as e:
        return f"? SSH execution failed: {e}"

HTML_TEMPLATE = '''
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Upload Excel File</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      background-color: #f5f7fa;
      margin: 0;
      padding: 20px;
      color: #333;
    }
    h2 {
      background-color: #003366;
      color: white;
      padding: 15px;
      border-radius: 4px;
    }
    form {
      background-color: white;
      padding: 20px;
      border: 1px solid #ddd;
      border-radius: 6px;
      margin-bottom: 30px;
    }
    input[type="file"] {
      margin-bottom: 10px;
    }
    input[type="submit"] {
      background-color: #0055aa;
      color: white;
      border: none;
      padding: 10px 20px;
      border-radius: 4px;
      cursor: pointer;
    }
    input[type="submit"]:hover {
      background-color: #003f7f;
    }
    .status-box {
      background-color: #e6f0ff;
      border-left: 6px solid #0055aa;
      padding: 10px 15px;
      margin-top: 20px;
      border-radius: 4px;
      white-space: pre-wrap;
    }
    table {
      border-collapse: collapse;
      width: 100%;
      margin-top: 20px;
    }
    th, td {
      border: 1px solid #ccc;
      padding: 8px 10px;
      text-align: left;
    }
    th {
      background-color: #003366;
      color: white;
    }
    tr:nth-child(even) {
      background-color: #f2f2f2;
    }
    tr:hover {
      background-color: #e1f0ff;
    }
  </style>
</head>
<body>

<h2>Network Fraud Detection Module UI</h2>

<form method="post" enctype="multipart/form-data" action="/upload">
  <label><strong>Excel File:</strong></label><br>
  <input type="file" name="excel" required><br><br>
  <input type="submit" value="Upload">
</form>

{% if table %}
  <h3>Processed Transaction Data:</h3>
  {{ table|safe }}
  <div class="status-box">
    <strong>Status:</strong><br>{{ save_note|safe }}
  </div>
{% endif %}

</body>
</html>
'''

@app.route('/', methods=['GET'])
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files.get('excel')
    if not file:
        return render_template_string(HTML_TEMPLATE, table=None)

    try:
        xls = pd.read_excel(file, sheet_name=None, dtype=str)
    except Exception as e:
        return f"<p><strong>? Failed to read Excel:</strong> {e}</p>"

    # Read sheets if they exist
    transaction_df = xls.get('transaction')
    nationalid_df = xls.get('nationalid')
    loan_df = xls.get('loan')

    for df in [transaction_df, nationalid_df, loan_df]:
        if df is not None:
            df.columns = df.columns.str.strip().str.replace(" ", "_")

    # Build national ID map
    nationalid_map = {}
    if nationalid_df is not None:
        nationalid_map = dict(zip(nationalid_df['National_ID'], nationalid_df['Customer_ID']))

    final_df = pd.DataFrame()
    if transaction_df is not None:
        def clean_and_join(value):
            if pd.isna(value):
                return ''
            if isinstance(value, float) and value.is_integer():
                value = str(int(value))
            else:
                value = str(value)
            return "_".join(value.strip().split())

        def generate_derived_id(row):
            tx_type = row['Transaction_Type']
            if tx_type in ['CREDIT_INT_TRANSFER', 'DEBIT_INT_TRANSFER']:
                return clean_and_join(row.get('CounterParty_ID'))
            elif tx_type == 'CASH_CREDIT':
                nat_id = row.get('National_ID')
                return nationalid_map.get(nat_id, f"{clean_and_join(row.get('Counterparty_Name'))}_{clean_and_join(nat_id)}")
            elif tx_type in ['CREDIT_EXT_TRANSFER', 'DEBIT_EXT_TRANSFER']:
                return f"{clean_and_join(row.get('Counterparty_Name'))}_{clean_and_join(row.get('Counterparty_Bank_Name'))}_{clean_and_join(row.get('Counterparty_Account_No'))}"
            return None

        transaction_df['Derived_Counterparty_ID'] = transaction_df.apply(generate_derived_id, axis=1)
        transaction_df.fillna('', inplace=True)

        final_columns = [
            'Transaction_Type',
            'Transaction_ID',
            'Trans_DT',
            'CounterParty_ID',
            'Customer_ID',
            'Amount',
            'Customer_Account_No',
            'Counterparty_Account_No',
            'Counterparty_Name',
            'Counterparty_Bank_Name',
            'Derived_Counterparty_ID'
        ]
        final_df = transaction_df[final_columns]

        save_note = []

        # Save and upload loan.csv
        if loan_df is not None:
            loan_path = os.path.join(UPLOAD_FOLDER, "loans.csv")
            loan_df.to_csv(loan_path, index=False)
            if upload_to_remote_server(loan_path, "loans.csv"):
                save_note.append("? loans.csv uploaded")

        # Save and upload transactions.csv
        tx_path = os.path.join(UPLOAD_FOLDER, "transactions.csv")
        final_df.to_csv(tx_path, index=False)
        if upload_to_remote_server(tx_path, "transactions.csv"):
            save_note.append("? transactions.csv uploaded")

        # Run remote script
        batch_result = run_remote_batch_script()
        save_note.append(batch_result)

        return render_template_string(
            HTML_TEMPLATE,
            table=final_df.to_html(index=False, classes="table table-bordered"),
            save_note="<br>".join(save_note)
        )

    return render_template_string(HTML_TEMPLATE, table=None)

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5050)
