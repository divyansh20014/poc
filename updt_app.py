from flask import Flask, request, render_template_string
import pandas as pd
import requests
import base64
import time
import os
import logging

app = Flask(__name__)

# Configure logging to stdout (for Kubernetes pod logs)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Define API endpoints
# FILE_API_MAP = {
#     'financial_file': 'http://10.225.13.208:32085/v1/financialtransaction',
#     'non_financial_file': 'http://10.225.13.208:32085/v1/nonfinancialtransaction',
#     'card_file': 'http://10.225.13.208:32085/v1/cardtransaction'
# }
FILE_API_MAP = {
    'financial_file': 'http://localhost:32085/v1/financialtransaction',
    'non_financial_file': 'http://localhost:32085/v1/nonfinancialtransaction',
    'card_file': 'http://localhost:32085/v1/cardtransaction'
}


# HTML upload form
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

        # Build base64-encoded Authorization header
        basic_token = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers = {
            "Authorization": f"Basic {basic_token}",
            "Content-Type": "application/json"
        }

        all_jsons = {}
        all_responses = []
        processed_files = 0

        for file_field, api_url in FILE_API_MAP.items():
            uploaded_file = request.files.get(file_field)
            if uploaded_file:
                filename = f'temp_{file_field}.xlsx'
                uploaded_file.save(filename)

                try:
                    df = pd.read_excel(filename)
                    if df.empty:
                        app.logger.warning(f"Uploaded file for {file_field} is empty.")
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
                    app.logger.error(f"Error reading {file_field}: {e}")
                finally:
                    if os.path.exists(filename):
                        os.remove(filename)

        # Send JSON to each API
        for file_field, api_info in all_jsons.items():
            api_url = api_info['api_url']
            json_list = api_info['data']

            for i, row_json in enumerate(json_list):
                try:
                    app.logger.info(f"\n➡ Sending to {api_url}: {row_json}")

                    response = requests.post(api_url, json=row_json, headers=headers)

                    app.logger.info(f"⬅ Received status: {response.status_code}")
                    app.logger.info(f"⬅ Received response content: {response.text}")

                    if response.status_code == 401:
                        return render_template_string(
                            HTML_TEMPLATE + "<p style='color:red;'>❌ Invalid username or password.</p>"
                        )

                    if response.headers.get('Content-Type', '').startswith('application/json'):
                        res_data = response.json()
                        # Store source and status for later display/debug
                        res_data['source'] = file_field
                        res_data['status_code'] = response.status_code
                        all_responses.append(res_data)
                    else:
                        all_responses.append({
                            'source': file_field,
                            'status_code': response.status_code,
                            'response': response.text
                        })

                except Exception as api_err:
                    app.logger.error(f"Error sending to API: {api_err}")
                    all_responses.append({
                        'source': file_field,
                        'status_code': 'error',
                        'response': str(api_err)
                    })

                time.sleep(1)  # avoid hammering the API

        if processed_files == 0:
            return render_template_string(HTML_TEMPLATE + "<p style='color:red;'>No valid Excel file uploaded.</p>")
        else:
            if all_responses:
                df_all = pd.json_normalize(all_responses)
                app.logger.info(f"Normalized response columns: {list(df_all.columns)}")

                # The fields you want to show from the response JSON
                selected_columns = [
                    'received.Customer.customerId',
                    'received.Transaction.transactionID',
                    'Fraud_Score',
                    'recommended_Action'
                ]
                rename_map = {
                    'received.Customer.customerId': 'CustomerID',
                    'received.Transaction.transactionID': 'TransactionID',
                    'Fraud_Score': 'Fraud_Score',
                    'recommended_Action': 'recommended_Action'
                }

                # Check which of these fields exist in the normalized DataFrame
                available_columns = [col for col in selected_columns if col in df_all.columns]
                app.logger.info(f"Available columns in response for UI: {available_columns}")

                if available_columns:
                    df_filtered = df_all[available_columns].rename(columns=rename_map)
                    response_table = df_filtered.to_html(index=False, border=1, justify="center")
                else:
                    response_table = "<p>No matching response fields found.</p>"
            else:
                response_table = "<p>No responses received.</p>"

            return render_template_string(
                HTML_TEMPLATE +
                f"<p style='color:green;'>{processed_files} file(s) processed successfully.</p><br><h3>API Responses:</h3>{response_table}"
            )

    return render_template_string(HTML_TEMPLATE)


if __name__ == '__main__':
    # Use debug=False in production to avoid auto-reload issues in containers
    app.run(debug=True)
