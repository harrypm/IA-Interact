import argparse
import os
import sys
import requests
from tqdm import tqdm
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import quote, urlparse


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

def upload_file_with_progress(identifier, file_path, directory):
    """
    Uploads a file to Internet Archive with progress tracking and retry logic.
    Uses 2MB chunks and retries up to 5 times for transient errors.
    """
    access_key = os.getenv("S3_ACCESS_KEY")
    secret_key = os.getenv("S3_SECRET_KEY")
    if not access_key or not secret_key:
        print("S3 access key or secret key is missing. Please set them as environment variables.")
        return False

    upload_url = f"https://s3.us.archive.org/{identifier}/{directory}/{os.path.basename(file_path)}"
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
        with tqdm(total=file_size, unit="B", unit_scale=True, desc=f"Uploading {os.path.basename(file_path)}") as pbar:
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
        print(f"Successfully uploaded {file_path}")
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

def list_repository_files(identifier):
    """
    Retrieves and lists files in a repository using the metadata API.
    Calls GET https://archive.org/metadata/{identifier} and parses the "files" array.
    Filters out any file with a path component (directory) that ends with ".thumbs".
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
    Downloads one or all files from a repository.
    """
    file_list = list_repository_files(identifier)
    if not file_list:
        return

    destination_dir_input = input("Enter destination folder (leave blank for current directory): ").strip().strip('\"').strip("'")
    destination_dir = os.path.abspath(os.path.expanduser(destination_dir_input)) if destination_dir_input else os.getcwd()

    try:
        os.makedirs(destination_dir, exist_ok=True)
    except OSError as e:
        print(f"Unable to create/access destination folder: {e}")
        return

    print("\nDownload Options:")
    print("1. Download a single file")
    print("2. Download all files")
    download_choice = input("Enter your choice (1 or 2): ").strip()

    if download_choice == "1":
        print("Enter the number of the file to download:")
        try:
            index = int(input().strip()) - 1
            if 0 <= index < len(file_list):
                download_file_with_progress(identifier, file_list[index], destination_dir)
            else:
                print("Invalid index.")
        except ValueError:
            print("Invalid input.")
    elif download_choice == "2":
        successful_downloads = 0
        for file_name in file_list:
            if download_file_with_progress(identifier, file_name, destination_dir):
                successful_downloads += 1
        print(f"\nDownloaded {successful_downloads} of {len(file_list)} files.")
    else:
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
   - Provide file paths to upload into a specified directory.
2. List files in a repository:
   - Retrieve and display files using the metadata API.
3. Delete a file from a repository:
   - List files and choose one to delete.
4. Move a file within a repository:
   - Copy a file to a new directory using x-amz-copy-source, then delete the original.
5. Download files from a repository:
   - Download one or all files to a local destination folder.
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
            print("Enter file paths (comma-separated):")
            files_input = input().strip()
            file_paths = [f.strip().strip('\"').strip("'") for f in files_input.split(",")]
            for file in file_paths:
                if not os.path.exists(file):
                    print(f"File not found: {file}")
                    return
            success = True
            for file_path in file_paths:
                if not upload_file_with_progress(identifier, file_path, directory):
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

def select_launch_mode(args):
    if args.gui:
        return "gui"
    if args.cli:
        return "cli"

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
    if select_launch_mode(args) == "gui":
        from ia_interact_gui import main as gui_main
        gui_main()
    else:
        main()
