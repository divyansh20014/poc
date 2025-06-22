from flask import Flask, request, render_template_string
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
import time
import os

app = Flask(__name__)

# Map upload field name to API endpoint
FILE_API_MAP = {
    'financial_file': 'http://localhost:32085/v1/financialtransaction',
    'non_financial_file': 'http://localhost:32085/v1/nonfinancialtransaction',
    'card_file': 'http://localhost:32085/v1/cardtransaction'
}

# FILE_API_MAP = {
#     'financial_file': 'http://10.13.225.107:32085/v1/financialtransaction',
#     'non_financial_file': 'http://10.13.225.107:32085/v1/nonfinancialtransaction',
#     'card_file': 'http://10.13.225.107:32085/v1/cardtransaction'
# }

# HTML form
HTML_TEMPLATE = '''<h2>Upload Excel Files</h2>
<form action="/" method="post" enctype="multipart/form-data">
    <label>Username:</label><br>
    <input type="text" name="username" required><br><br>

    <label>Password:</label><br>
    <input type="password" name="password" required><br><br>

    <label>Upload Financial File:</label><br>
    <input type="file" name="financial_file" accept=".xlsx"><br><br>

    <label>Upload Non-Financial File:</label><br>
    <input type="file" name="non_financial_file" accept=".xlsx"><br><br>

    <label>Upload Card File:</label><br>
    <input type="file" name="card_file" accept=".xlsx"><br><br>

    <input type="submit" value="Upload & Send">
</form>'''

def nest_keys(flat_dict):
    nested = {}
    for key, value in flat_dict.items():
        if '.' in key:
            parts = key.split('.')
            d = nested
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = value
        else:
            nested[key] = value
    return nested


@app.route('/', methods=['GET', 'POST'])
def upload_excel():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            return render_template_string(HTML_TEMPLATE + "<p style='color:red;'>Username and password are required.</p>")

        auth = HTTPBasicAuth(username, password)
        all_jsons = {}
        all_responses = []
        processed_files = 0

        # Read Excel files and prepare JSONs
        for file_field, api_url in FILE_API_MAP.items():
            uploaded_file = request.files.get(file_field)
            if uploaded_file:
                filename = f'temp_{file_field}.xlsx'
                uploaded_file.save(filename)

                try:
                    df = pd.read_excel(filename)
                    if df.empty:
                        continue

                    nested_json_list = []
                    for _, row in df.iterrows():
                        flat_data = row.fillna('').to_dict()
                        nested_json = nest_keys(flat_data)
                        nested_json_list.append(nested_json)

                    all_jsons[file_field] = {
                        'api_url': api_url,
                        'data': nested_json_list
                    }
                    processed_files += 1
                except Exception as e:
                    print(f"Error reading {file_field}: {e}")
                finally:
                    if os.path.exists(filename):
                        os.remove(filename)

        # Send JSONs to respective APIs
        for file_field, api_info in all_jsons.items():
            api_url = api_info['api_url']
            json_list = api_info['data']

            for i, row_json in enumerate(json_list):
                try:
                    print(f"\n📤 Sending to {api_url}:")
                    print(row_json)

                    response = requests.post(api_url, json=row_json, auth=auth)

                    # Check for invalid credentials
                    if response.status_code == 401:
                        return render_template_string(
                            HTML_TEMPLATE + "<p style='color:red;'>❌ Invalid username or password. Authentication failed.</p>"
                        )
                    if response.headers.get('Content-Type', '').startswith('application/json'):
                        res_data = response.json()
                        res_data['source'] = file_field
                        res_data['status_code'] = response.status_code
                        all_responses.append(res_data)

                        # ✅ Show response in terminal
                        print("✅ Response from API:")
                        print(res_data)
                    else:
                        print("⚠️ Non-JSON Response:")
                        print(response.text)
                        all_responses.append({
                            'source': file_field,
                            'status_code': response.status_code,
                            'response': response.text
                        })

                except Exception as api_err:
                    print("❌ Error while sending:")
                    print(api_err)
                    all_responses.append({
                        'source': file_field,
                        'status_code': 'error',
                        'response': str(api_err)
                    })

                time.sleep(1)

        # Show selected fields in HTML table
        if processed_files == 0:
            return render_template_string(HTML_TEMPLATE + "<p style='color:red;'>No valid Excel file uploaded.</p>")
        else:
            if all_responses:
                df_all = pd.json_normalize(all_responses, sep='.')
                selected_columns = [
                    'received.Customer.customerId',
                    'received.Transaction.transactionID',
                    'received.Transaction.transactionType',
                    # 'fraudScore',
                    # 'recommendedAction'
                ]
                rename_map = {
                    'received.Customer.customerId': 'CustomerID',
                    'received.Transaction.transactionID': 'TransactionID',
                    'received.Transaction.transactionType': 'TransactionType',
                    # 'fraudScore': 'FraudScore',
                    # 'recommendedAction': 'RecommendedAction'
                }

                available_columns = [col for col in selected_columns if col in df_all.columns]
                df_filtered = df_all[available_columns].rename(columns=rename_map)
                response_table = df_filtered.to_html(index=False, border=1, justify="center")
            else:
                response_table = "<p>No responses received.</p>"

            return render_template_string(
                HTML_TEMPLATE +
                f"<p style='color:green;'>{processed_files} file(s) processed and sent successfully.</p><br><h3>API Responses:</h3>{response_table}"
            )

    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    app.run(debug=True)