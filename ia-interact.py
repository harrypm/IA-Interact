import argparse
import os
import sys
import requests
from tqdm import tqdm
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import quote, urlparse

from version import __version__


def _extract_identifier_from_archive_url(value):
    candidate = value
    if "://" not in candidate and candidate.startswith("archive.org/"):
        candidate = f"https://{candidate}"

    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None

    host = (parsed.hostname or "").lower()
    if host not in ("archive.org", "www.archive.org"):
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        return None

    if path_parts[0] in ("details", "download", "metadata"):
        if len(path_parts) < 2:
            return None
        return path_parts[1]

    return path_parts[0]

def get_repo_identifier(repo_link):
    """
    Extracts the repository identifier from an Internet Archive link or raw identifier.
    Supports archive.org/details, archive.org/download, archive.org/metadata, and plain identifiers.
    """
    value = repo_link.strip().strip('"').strip("'")
    if not value:
        print("Invalid Internet Archive URL. Please provide a valid link or identifier.")
        return None

    identifier = _extract_identifier_from_archive_url(value)
    if identifier:
        return identifier

    if "/" not in value and " " not in value:
        return value

    print("Invalid Internet Archive URL. Please provide a valid link or identifier.")
    return None

def collect_folder_files(folder_path):
    """
    Recursively collects files under folder_path.

    Returns (entries, errors). Each entry is a dict with:
      - "local_path": absolute path of the file on disk
      - "rel": the file path relative to folder_path, using "/" separators
        (the folder's own name is NOT included; callers prepend it if wanted).
    errors is a list of (path, message) tuples for directories that could not
    be read; the walk continues past them.
    """
    entries = []
    errors = []

    def on_error(err):
        errors.append((getattr(err, "filename", None) or folder_path, str(err)))

    for root, dirs, files in os.walk(folder_path, onerror=on_error):
        dirs.sort()
        for file_name in sorted(files):
            file_path = os.path.join(root, file_name)
            rel = os.path.relpath(file_path, folder_path).replace(os.sep, "/")
            if not rel or rel == "." or rel.startswith("../"):
                continue
            entries.append({"local_path": os.path.abspath(file_path), "rel": rel})
    return entries, errors

def build_download_listing(file_paths):
    """
    Groups a flat "a/b/c" file list into directory-style rows for display.

    Returns rows as dicts: {"type": "file"|"folder", "label", "depth", "path",
    "file_indices"}. Folders are listed before files at each level, both
    alphabetical; a folder row is followed by its own subtree, and carries the
    flat indices of every file it contains so a whole folder can be selected.
    """
    root = {"dirs": {}, "files": []}
    for index, path in enumerate(file_paths):
        parts = [part for part in path.split("/") if part]
        if not parts:
            continue
        node = root
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "files": []})
        node["files"].append((index, path))

    def build(node, depth, prefix):
        rows = []
        all_indices = []
        for name in sorted(node["dirs"], key=str.lower):
            child_path = f"{prefix}{name}/"
            child_rows, child_indices = build(node["dirs"][name], depth + 1, child_path)
            rows.append(
                {
                    "type": "folder",
                    "label": f"{name}/",
                    "depth": depth,
                    "path": child_path,
                    "file_indices": child_indices,
                }
            )
            rows.extend(child_rows)
            all_indices.extend(child_indices)
        for index, path in sorted(node["files"], key=lambda item: item[1].lower()):
            name = path.rsplit("/", 1)[-1]
            rows.append(
                {
                    "type": "file",
                    "label": name,
                    "depth": depth,
                    "path": path,
                    "file_indices": [index],
                }
            )
            all_indices.append(index)
        return rows, all_indices

    rows, _ = build(root, 0, "")
    return rows

def upload_file_with_progress(identifier, file_path, directory, object_name=None):
    """
    Uploads a file to Internet Archive with progress tracking and retry logic.
    Uses 2MB chunks and retries up to 5 times for transient errors.
    object_name is the repository-side path for the upload ('/'-separated
    subdirectories are allowed for folder uploads); it defaults to the file's
    basename.
    """
    access_key = os.getenv("S3_ACCESS_KEY")
    secret_key = os.getenv("S3_SECRET_KEY")
    if not access_key or not secret_key:
        print("S3 access key or secret key is missing. Please set them as environment variables.")
        return False

    object_name = object_name or os.path.basename(file_path)
    object_path = f"{directory}/{object_name}" if directory else object_name
    upload_url = f"https://s3.us.archive.org/{identifier}/{quote(object_path, safe='/')}"
    file_size = os.path.getsize(file_path)

    retry_strategy = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["PUT"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("https://", adapter)

    headers = {
        "x-amz-auto-make-bucket": "1",
        "Authorization": f"AWS {access_key}:{secret_key}"
    }

    try:
        with tqdm(total=file_size, unit="B", unit_scale=True, desc=f"Uploading {object_name}") as pbar:
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024 * 2)  # 2MB chunks
                    if not chunk:
                        break
                    response = session.put(upload_url, headers=headers, data=chunk, timeout=(60, 600))
                    if response.status_code != 200:
                        print(f"Error uploading {file_path}: {response.status_code} {response.reason}")
                        return False
                    pbar.update(len(chunk))
        print(f"Successfully uploaded {file_path} -> {object_path}")
        return True
    except requests.exceptions.SSLError as e:
        print(f"SSL Error during upload: {e}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Request error: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False

def list_repository_files(identifier, display=True):
    """
    Retrieves and lists files in a repository using the metadata API.
    Calls GET https://archive.org/metadata/{identifier} and parses the "files" array.
    Filters out any file with a path component (directory) that ends with ".thumbs".
    When display is False the numbered listing is not printed (used by the
    download flow, which prints its own directory-style listing).
    """
    url = f"https://archive.org/metadata/{identifier}"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Error fetching metadata: {response.status_code} {response.reason}")
            return []
        data = response.json()
        files = data.get("files", [])
        file_list = []
        for f in files:
            if "name" in f:
                name = f.get("name")
                # Split the path into components and exclude if any component ends with ".thumbs"
                parts = name.split("/")
                if any(part.endswith(".thumbs") for part in parts):
                    continue
                file_list.append(name)
        if display:
            if file_list:
                print("Files in repository:")
                for idx, name in enumerate(file_list, start=1):
                    print(f"{idx}. {name}")
            else:
                print("No files found in repository.")
        return file_list
    except Exception as e:
        print("Error listing files:", e)
        return []

def delete_file(identifier, file_path):
    """
    Deletes a file from the repository via an HTTP DELETE request.
    """
    access_key = os.getenv("S3_ACCESS_KEY")
    secret_key = os.getenv("S3_SECRET_KEY")
    if not access_key or not secret_key:
        print("S3 keys missing. Cannot delete file.")
        return False
    delete_url = f"https://s3.us.archive.org/{identifier}/{file_path}"
    headers = {
        "x-amz-auto-make-bucket": "1",
        "Authorization": f"AWS {access_key}:{secret_key}"
    }
    try:
        response = requests.delete(delete_url, headers=headers)
        if response.status_code in (200, 204):
            print(f"File '{file_path}' deleted successfully.")
            return True
        else:
            print(f"Failed to delete {file_path}: {response.status_code} {response.reason}")
            return False
    except Exception as e:
        print("Error during deletion:", e)
        return False

def move_file(identifier, file_name, source_dir, target_dir):
    """
    Moves a file within the repository by copying it to the target directory and deleting the original.
    Uses the x-amz-copy-source header for copying.
    """
    access_key = os.getenv("S3_ACCESS_KEY")
    secret_key = os.getenv("S3_SECRET_KEY")
    if not access_key or not secret_key:
        print("S3 keys missing. Cannot move file.")
        return False

    source_path = f"{source_dir}/{file_name}" if source_dir else file_name
    destination_path = f"{target_dir}/{file_name}" if target_dir else file_name

    copy_url = f"https://s3.us.archive.org/{identifier}/{destination_path}"
    source_url = f"/{identifier}/{source_path}"
    headers = {
        "x-amz-auto-make-bucket": "1",
        "Authorization": f"AWS {access_key}:{secret_key}",
        "x-amz-copy-source": source_url
    }
    try:
        response = requests.put(copy_url, headers=headers)
        if response.status_code not in (200, 201):
            print(f"Error copying file from {source_path} to {destination_path}: {response.status_code} {response.reason}")
            return False
        print(f"File copied from {source_path} to {destination_path} successfully.")
        if delete_file(identifier, source_path):
            print(f"File moved from {source_path} to {destination_path} successfully.")
            return True
        else:
            print(f"File copied but failed to delete original {source_path}.")
            return False
    except Exception as e:
        print("Error during file move (copy-delete):", e)
        return False
def download_file_with_progress(identifier, file_name, destination_dir):
    """
    Downloads a file from an Internet Archive repository with progress tracking.
    """
    safe_relative_path = os.path.normpath(file_name).lstrip("/\\")
    if safe_relative_path.startswith(".."):
        print(f"Skipping unsafe file path: {file_name}")
        return False

    download_url = f"https://archive.org/download/{identifier}/{quote(file_name, safe='/')}"
    output_path = os.path.join(destination_dir, safe_relative_path)
    output_folder = os.path.dirname(output_path)

    if output_folder:
        os.makedirs(output_folder, exist_ok=True)

    try:
        response = requests.get(download_url, stream=True, timeout=(60, 600))
        if response.status_code != 200:
            print(f"Error downloading {file_name}: {response.status_code} {response.reason}")
            return False

        total_size = int(response.headers.get("content-length", 0))
        progress_total = total_size if total_size > 0 else None

        with tqdm(total=progress_total, unit="B", unit_scale=True, desc=f"Downloading {os.path.basename(file_name)}") as pbar:
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    pbar.update(len(chunk))

        print(f"Downloaded '{file_name}' to '{output_path}'")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Request error during download: {e}")
        return False
    except Exception as e:
        print(f"Unexpected download error: {e}")
        return False
def download_repository_files(identifier):
    """
    Downloads a single file, a whole folder, or all files from a repository.
    The listing is shown directory-style: folders (with file counts) followed
    by their contents; entering a folder's number downloads every file it
    contains.
    """
    file_list = list_repository_files(identifier, display=False)
    if not file_list:
        print("No files found in repository.")
        return

    rows = build_download_listing(file_list)
    print("\nRepository contents (pick a number to download a file or a whole folder):")
    for number, row in enumerate(rows, start=1):
        indent = "    " * row["depth"]
        if row["type"] == "folder":
            count = len(row["file_indices"])
            plural = "s" if count != 1 else ""
            print(f"{number}. {indent}{row['label']} ({count} file{plural})")
        else:
            print(f"{number}. {indent}{row['label']}")

    destination_dir_input = input("\nEnter destination folder (leave blank for current directory): ").strip().strip('"').strip("'")
    destination_dir = os.path.abspath(os.path.expanduser(destination_dir_input)) if destination_dir_input else os.getcwd()

    try:
        os.makedirs(destination_dir, exist_ok=True)
    except OSError as e:
        print(f"Unable to create/access destination folder: {e}")
        return

    print("\nDownload Options:")
    print("1. Download a single file or a whole folder")
    print("2. Download all files")
    download_choice = input("Enter your choice (1 or 2): ").strip()

    if download_choice == "1":
        print("Enter the number of the file or folder to download:")
        try:
            number = int(input().strip())
        except ValueError:
            print("Invalid input.")
            return
        if not 1 <= number <= len(rows):
            print("Invalid number.")
            return
        row = rows[number - 1]
        if row["type"] == "folder":
            selected_files = [file_list[i] for i in row["file_indices"]]
            print(f"Downloading folder '{row['path']}' ({len(selected_files)} file(s))...")
        else:
            selected_files = [row["path"]]
        successful_downloads = 0
        for file_name in selected_files:
            if download_file_with_progress(identifier, file_name, destination_dir):
                successful_downloads += 1
        print(f"\nDownloaded {successful_downloads} of {len(selected_files)} file(s).")
    elif download_choice == "2":
        successful_downloads = 0
        for file_name in file_list:
            if download_file_with_progress(identifier, file_name, destination_dir):
                successful_downloads += 1
        print(f"\nDownloaded {successful_downloads} of {len(file_list)} files.")
    else:
        print("Invalid choice. Exiting download menu.")
        print("Invalid choice. Exiting download menu.")

def create_rules_file(folder_path):
    """
    Creates a _rules.conf file in the specified folder, if it does not exist.
    """
    rules_path = os.path.join(folder_path, "_rules.conf")
    if not os.path.exists(rules_path):
        print("\nThe folder does not contain a '_rules.conf' file. Creating one...")
        with open(rules_path, "w") as rules_file:
            rules_file.write("CAT.ALL\n")
        print("Created '_rules.conf' with contents:\nCAT.ALL")
    else:
        print("\nThe '_rules.conf' file already exists. Skipping creation.")

def prompt_metadata():
    """
    Prompts the user for metadata required when creating a new repository.
    """
    print("\nProvide the following metadata for the new repository:")
    metadata = {
        "title": input("Title: ").strip(),
        "description": input("Description: ").strip(),
        "creator": input("Creator (optional): ").strip(),
        "date": input("Date (optional, e.g., YYYY-MM-DD): ").strip(),
        "language": input("Language (optional, e.g., eng): ").strip(),
        "licenseurl": input("License URL (optional): ").strip(),
    }
    print("\nCollection Options (choose one):")
    print("1. community - General-purpose collection for user-contributed materials")
    print("2. opensource - Collection for open-source software")
    print("3. texts - Books, magazines, and other written documents")
    print("4. movies - Videos, movies, and visual media")
    print("5. audio - Audio recordings, including music and podcasts")
    print("6. image - Images or photography")
    print("7. etree - Live music archive for etree community recordings")
    print("8. folksoundomy - Independent and user-contributed audio content")
    print("9. games - Video games and gaming-related resources")
    print("10. software - Software applications and tools")
    collection_input = input("Enter the name of the collection from the list above: ").strip()
    metadata["collection"] = collection_input
    metadata["subject"] = input("Subject tags (comma-separated, e.g., music, history): ").strip()
    print("\nNote: If you select 'yes' for test item, the repository will be automatically deleted after 30 days.")
    metadata["test_item"] = input("Is this a test item? (yes/no): ").strip().lower()
    if metadata["test_item"] == "yes":
        metadata["test_item"] = "true"
    elif metadata["test_item"] == "no":
        del metadata["test_item"]
    else:
        print("Invalid input for test item. Assuming 'no'.")
        del metadata["test_item"]
    return {key: value for key, value in metadata.items() if value}

def initialize_repository(folder_path, identifier, metadata, mode):
    """
    Uploads an entire folder as a new repository.
    In Test Mode, simulates the actions without uploading; otherwise, uploads each file via HTTP PUT.
    """
    access_key = os.getenv("S3_ACCESS_KEY")
    secret_key = os.getenv("S3_SECRET_KEY")
    if not access_key or not secret_key:
        print("S3 access key or secret key is missing. Please set them as environment variables.")
        return

    if mode == "Test":
        print("\nTEST MODE: Simulating repository creation...")
        print("Folder path:", folder_path)
        print("Identifier:", identifier)
        print("Metadata:", metadata)
        print("Files that would be uploaded:")
        for root, _, files in os.walk(folder_path):
            for file_name in files:
                relative_path = os.path.relpath(os.path.join(root, file_name), folder_path)
                print(" -", relative_path)
        print("\nNo files uploaded. Test mode complete.")
        return

    for root, _, files in os.walk(folder_path):
        for file_name in files:
            file_path = os.path.join(root, file_name)
            relative_path = os.path.relpath(file_path, folder_path)
            upload_url = f"https://s3.us.archive.org/{identifier}/{relative_path}"
            headers = {
                "x-amz-auto-make-bucket": "1",
                "Authorization": f"AWS {access_key}:{secret_key}"
            }
            try:
                print("\nUploading file:", relative_path)
                file_size = os.path.getsize(file_path)
                with tqdm(total=file_size, unit="B", unit_scale=True, desc=f"Uploading {relative_path}") as pbar:
                    with open(file_path, "rb") as file_data:
                        response = requests.put(upload_url, headers=headers, data=file_data)
                        if response.status_code != 200:
                            print(f"Error uploading {relative_path}: {response.status_code} {response.reason}")
                            return
                        pbar.update(file_size)
                print(f"File '{relative_path}' uploaded successfully.")
            except Exception as e:
                print("Error during upload:", e)

    metadata_url = f"https://archive.org/metadata/{identifier}/metadata"
    try:
        print("\nAdding metadata to the repository...")
        response = requests.post(metadata_url, data=metadata)
        if response.status_code == 200:
            print("Metadata added successfully.")
        else:
            print(f"Error adding metadata: {response.status_code} {response.reason}")
    except Exception as e:
        print("Error during metadata addition:", e)

def print_help():
    """
    Prints help information for IA Interact.
    For more details, refer to: https://archive.org/developers/internetarchive/cli.html
    """
    help_text = """
IA Interact - Help

Main Menu Options:
1. Upload files to an existing repository:
   - Provide file and/or folder paths to upload into a specified directory.
   - Folders are uploaded recursively and their structure is preserved.
2. List files in a repository:
   - Retrieve and display files using the metadata API.
3. Delete a file from a repository:
   - List files and choose one to delete.
4. Move a file within a repository:
   - Copy a file to a new directory using x-amz-copy-source, then delete the original.
5. Download files from a repository:
   - Pick a file or a whole folder from the directory-style listing,
     or download all files, to a local destination folder.
6. Create or access a repository from a folder:
   - Upload an entire folder as a new repository with metadata and mode selection.
7. Help:
   - Display this help information.

Instructions:
- Ensure S3 credentials are set as environment variables.
- Follow the prompts for each option.
    """
    print(help_text)

def main():
    """
    Main menu for IA Interact.
    Displays options first and asks for repository URL only when necessary.
    """
    print("Welcome to IA Interact!")
    print("Select an option:")
    print("1. Upload files to an existing repository")
    print("2. List files in a repository")
    print("3. Delete a file from a repository")
    print("4. Move a file within a repository")
    print("5. Download files from a repository")
    print("6. Create or access a repository from a folder")
    print("7. Help")
    choice = input("Enter your choice (1-7): ").strip()

    if choice == "7":
        print_help()
        main()
    elif choice == "6":
        folder_path = input("Enter the folder path to upload as a repository: ").strip().strip('\"').strip('\'')
        if not os.path.isdir(folder_path):
            print("Invalid folder path. Please check if the directory exists and is accessible.")
            return
        print("\nChoose mode:")
        print("1. Test Mode (simulate actions)")
        print("2. Permanent Mode (execute actions)")
        mode_choice = input("Enter your choice (1 or 2): ").strip()
        if mode_choice == "1":
            mode = "Test"
        elif mode_choice == "2":
            mode = "Permanent"
        else:
            print("Invalid choice. Exiting.")
            return
        metadata = prompt_metadata()
        create_rules_file(folder_path)
        identifier = input("Enter a unique identifier for the new repository: ").strip()
        if not identifier:
            print("Repository identifier is required.")
            return
        initialize_repository(folder_path, identifier, metadata, mode)
    elif choice in ("1", "2", "3", "4", "5"):
        repo_link = input("Enter the Internet Archive repository link or identifier: ").strip()
        identifier = get_repo_identifier(repo_link)
        if not identifier:
            return
        if choice == "1":
            print("\nUpload Files:")
            print("1. Add files to an existing directory")
            print("2. Create a new directory and upload files")
            dir_choice = input("Enter your choice (1 or 2): ").strip()
            if dir_choice == "1":
                directory = input("Enter the name of the existing directory to use: ").strip()
            elif dir_choice == "2":
                directory = input("Enter the name of the new directory to create: ").strip()
            else:
                print("Invalid choice. Exiting.")
                return
            print("Enter file and/or folder paths (comma-separated).")
            print("Folders are uploaded recursively and their structure is preserved.")
            paths_input = input().strip()
            paths = [p.strip().strip('"').strip("'") for p in paths_input.split(",") if p.strip()]
            upload_jobs = []  # (local_path, repository-side object name)
            for path in paths:
                if os.path.isdir(path):
                    folder_name = os.path.basename(os.path.normpath(path)) or "folder"
                    entries, errors = collect_folder_files(path)
                    for error_path, error_message in errors:
                        print(f"Warning: could not read {error_path}: {error_message}")
                    if not entries:
                        print(f"No files found in folder: {path}")
                        return
                    print(f"Folder '{path}': {len(entries)} file(s) will be uploaded under '{folder_name}/'")
                    for entry in entries:
                        upload_jobs.append((entry["local_path"], f"{folder_name}/{entry['rel']}"))
                elif os.path.isfile(path):
                    upload_jobs.append((path, os.path.basename(path)))
                else:
                    print(f"Path not found: {path}")
                    return
            if not upload_jobs:
                print("No paths entered.")
                return
            success = True
            for local_path, object_name in upload_jobs:
                if not upload_file_with_progress(identifier, local_path, directory, object_name=object_name):
                    success = False
            if success:
                print("\nAll files uploaded successfully!")
            else:
                print("\nSome files failed to upload. Please check the errors and try again.")
        elif choice == "2":
            list_repository_files(identifier)
        elif choice == "3":
            file_list = list_repository_files(identifier)
            if not file_list:
                return
            print("Enter the number of the file to delete:")
            try:
                index = int(input().strip()) - 1
                if 0 <= index < len(file_list):
                    delete_file(identifier, file_list[index])
                else:
                    print("Invalid index.")
            except ValueError:
                print("Invalid input.")
        elif choice == "4":
            file_list = list_repository_files(identifier)
            if not file_list:
                return
            print("Enter the number of the file to move:")
            try:
                index = int(input().strip()) - 1
                if 0 <= index < len(file_list):
                    file_to_move = file_list[index]
                    source_dir = input("Enter the current directory of the file (or leave blank for root): ").strip()
                    target_dir = input("Enter the target directory: ").strip()
                    move_file(identifier, file_to_move, source_dir, target_dir)
                else:
                    print("Invalid index.")
            except ValueError:
                print("Invalid input.")
        elif choice == "5":
            download_repository_files(identifier)
    else:
        print("Invalid choice. Exiting.")


def parse_args():
    parser = argparse.ArgumentParser(description="IA Interact CLI/GUI launcher")
    parser.add_argument(
        "--version",
        action="version",
        version=f"IA Interact {__version__}",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--gui",
        action="store_true",
        help="Launch the GUI instead of the interactive CLI menu."
    )
    mode_group.add_argument(
        "--cli",
        action="store_true",
        help="Force the interactive CLI menu."
    )
    return parser.parse_args()

def _windows_launched_by_explorer():
    """Best-effort: True on Windows when the parent process is explorer.exe.

    A Windows console-subsystem executable (PyInstaller ``--onefile`` without
    ``--windowed``) gets a freshly-allocated console when double-clicked in
    Explorer, so ``sys.stdin.isatty()`` is True even though the user did NOT
    open a terminal. That would otherwise make ``select_launch_mode`` pick the
    CLI text menu instead of the GUI. Detecting an Explorer parent lets us
    route double-clicks to the GUI.

    Returns False on non-Windows platforms and on any error (safe fallback:
    behave like a normal terminal launch).
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),  # ULONG_PTR
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        TH32CS_SNAPPROCESS = 0x00000002
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.GetCurrentProcessId.argtypes = []
        kernel32.GetCurrentProcessId.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            our_pid = kernel32.GetCurrentProcessId()
            parent_pid = None
            if kernel32.Process32FirstW(ctypes.byref(entry)):
                while True:
                    if entry.th32ProcessID == our_pid:
                        parent_pid = entry.th32ParentProcessID
                        break
                    if not kernel32.Process32NextW(ctypes.byref(entry)):
                        break
            if parent_pid is None:
                return False
            parent_name = ""
            if kernel32.Process32FirstW(ctypes.byref(entry)):
                while True:
                    if entry.th32ProcessID == parent_pid:
                        parent_name = entry.szExeFile
                        break
                    if not kernel32.Process32NextW(ctypes.byref(entry)):
                        break
            return parent_name.lower() == "explorer.exe"
        finally:
            try:
                kernel32.CloseHandle(snapshot)
            except Exception:
                pass
    except Exception:
        return False


def _windows_set_console_visible(visible):
    """Hide or show this process's own console window on Windows (no-op elsewhere)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32.GetConsoleWindow.argtypes = []
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.ShowWindow.restype = ctypes.c_int
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            # SW_HIDE = 0, SW_SHOW = 5
            user32.ShowWindow(hwnd, 5 if visible else 0)
    except Exception:
        pass


def select_launch_mode(args, explorer_launch=False):
    if args.gui:
        return "gui"
    if args.cli:
        return "cli"

    # A Windows console-exe double-clicked in Explorer gets an auto-allocated
    # console (so isatty() is True), but the user clearly wants the GUI.
    if explorer_launch:
        return "gui"

    interactive_terminal = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive_terminal:
        return "cli"

    has_graphical_session = (
        bool(os.environ.get("DISPLAY"))
        or bool(os.environ.get("WAYLAND_DISPLAY"))
        or sys.platform in ("win32", "darwin")
    )
    if has_graphical_session:
        return "gui"

    return "cli"


if __name__ == "__main__":
    args = parse_args()
    explorer_launch = _windows_launched_by_explorer()
    if select_launch_mode(args, explorer_launch=explorer_launch) == "gui":
        # Only hide the throwaway console for an Explorer (double-click) launch
        # that auto-selected the GUI; never for an explicit --gui from a real
        # terminal (that would yank the user's terminal window away).
        hide_console = explorer_launch and not args.gui
        if hide_console:
            _windows_set_console_visible(False)
        try:
            from ia_interact_gui import main as gui_main
            gui_main()
        except Exception:
            import traceback
            if hide_console:
                _windows_set_console_visible(True)
            traceback.print_exc()
            raise
    else:
        main()
