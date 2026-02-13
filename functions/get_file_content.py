import os

def get_file_content(working_directory, file_path):
    try:
        abs_working_dir = os.path.abspath(working_directory)
        target_file = os.path.normpath(os.path.join(abs_working_dir, file_path))
        valid_target_file = os.path.commonpath([abs_working_dir, target_file]) == abs_working_dir

        target_is_file = os.path.isfile(target_file)

        if not valid_target_file:
            return f'Error: Cannot list "{file_path}" as it is outside the permitted working directory'
        elif not target_is_file:
            return f'Error: File not found or is not a regular file: "{file_path}"'

    except Exception as e:
        return f"Error reading file: {e}"