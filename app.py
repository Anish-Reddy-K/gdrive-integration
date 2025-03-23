import os
from flask import Flask, redirect, url_for, session, request, render_template, flash
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import googleapiclient.http

# Flask app setup
app = Flask(__name__)
app.secret_key = 'YOUR_SECRET_KEY'  # Replace with a secure key

# For development only – allows HTTP (remove for production)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# Google API configuration
CLIENT_SECRETS_FILE = 'client_secret.json'
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Allowed MIME types for documents:
ALLOWED_MIME_TYPES = [
    "application/vnd.google-apps.document",              # Google Docs
    "application/vnd.google-apps.spreadsheet",             # Google Sheets
    "application/vnd.google-apps.presentation",            # Google Slides
    "application/pdf",                                     # PDF files
    "application/msword",                                  # MS Word (older)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # MS Word (newer)
    "application/vnd.openxmlformats-officedocument.presentationml.presentation" # MS PowerPoint
]

def build_query_for_allowed_files():
    # Build an OR query for allowed MIME types.
    mime_queries = [f"mimeType='{mime}'" for mime in ALLOWED_MIME_TYPES]
    return "(" + " or ".join(mime_queries) + ")"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/authorize')
def authorize():
    redirect_uri = url_for('oauth2callback', _external=True)
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',  # Request a refresh token
        include_granted_scopes='true'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session.get('state')
    redirect_uri = url_for('oauth2callback', _external=True)
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=state,
        redirect_uri=redirect_uri
    )
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials

    # Store credentials in session for subsequent requests
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    return redirect(url_for('list_folders'))

def get_drive_service():
    if 'credentials' not in session:
        return None
    creds = Credentials(**session['credentials'])
    service = build('drive', 'v3', credentials=creds)
    return service

@app.route('/list_folders')
def list_folders():
    """List folders so users can pick one (with checkboxes for bulk actions)."""
    drive_service = get_drive_service()
    if drive_service is None:
        return redirect(url_for('authorize'))
    
    # Query for folders
    results = drive_service.files().list(
        q="mimeType='application/vnd.google-apps.folder'",
        pageSize=50,
        fields="files(id, name)"
    ).execute()
    folders = results.get('files', [])
    return render_template('folders.html', folders=folders)

@app.route('/list_files')
@app.route('/list_files/<folder_id>')
def list_files(folder_id=None):
    """List allowed document files, either from root or a specific folder, with checkboxes."""
    drive_service = get_drive_service()
    if drive_service is None:
        return redirect(url_for('authorize'))
    
    query = build_query_for_allowed_files()
    if folder_id:
        # Limit to files inside the folder
        query = f"'{folder_id}' in parents and {query}"
    
    results = drive_service.files().list(
        q=query,
        pageSize=100,
        fields="files(id, name, mimeType)"
    ).execute()
    files = results.get('files', [])
    return render_template('files.html', files=files, folder_id=folder_id)

def download_file_by_id(drive_service, file_id):
    """Helper function to download a file by its ID."""
    file_meta = drive_service.files().get(fileId=file_id, fields="name, mimeType").execute()
    file_name = file_meta.get('name')
    download_dir = os.path.join('downloads', file_id)
    os.makedirs(download_dir, exist_ok=True)
    file_path = os.path.join(download_dir, file_name)
    request_file = drive_service.files().get_media(fileId=file_id)
    with open(file_path, 'wb') as fh:
        downloader = googleapiclient.http.MediaIoBaseDownload(fh, request_file)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            print(f"Downloading {file_name}: {int(status.progress() * 100)}%")
    return file_name

@app.route('/download/<file_id>')
def download_file(file_id):
    """Single file download."""
    drive_service = get_drive_service()
    if drive_service is None:
        return redirect(url_for('authorize'))
    file_name = download_file_by_id(drive_service, file_id)
    flash(f"Downloaded {file_name}.")
    return redirect(url_for('list_files'))

@app.route('/download_files', methods=['POST'])
def download_files():
    """Download selected files (bulk action)."""
    drive_service = get_drive_service()
    if drive_service is None:
        return redirect(url_for('authorize'))
    selected_files = request.form.getlist('selected_files')
    download_count = 0
    for file_id in selected_files:
        download_file_by_id(drive_service, file_id)
        download_count += 1
    flash(f"Downloaded {download_count} file(s).")
    return redirect(url_for('list_files'))

@app.route('/download_folders', methods=['POST'])
def download_folders():
    """Download allowed files from selected folders (bulk action)."""
    drive_service = get_drive_service()
    if drive_service is None:
        return redirect(url_for('authorize'))
    selected_folders = request.form.getlist('selected_folders')
    total_files_downloaded = 0
    for folder_id in selected_folders:
        query = f"'{folder_id}' in parents and {build_query_for_allowed_files()}"
        results = drive_service.files().list(
            q=query,
            pageSize=100,
            fields="files(id, name, mimeType)"
        ).execute()
        files = results.get('files', [])
        for file in files:
            file_id = file['id']
            download_file_by_id(drive_service, file_id)
            total_files_downloaded += 1
    flash(f"Downloaded {total_files_downloaded} file(s) from selected folder(s).")
    return redirect(url_for('list_folders'))

if __name__ == '__main__':
    app.run(debug=True)