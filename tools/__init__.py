from tools.filesystem import (
    write_file,
    read_file,
    create_folder,
    delete_path,
    WRITE_FILE_SCHEMA,
    READ_FILE_SCHEMA,
    CREATE_FOLDER_SCHEMA,
    DELETE_PATH_SCHEMA
)

from tools.execution import (
    run_code,
    RUN_CODE_SCHEMA
)

from tools.system import (
    launch_app,
    close_app,
    LAUNCH_APP_SCHEMA,
    CLOSE_APP_SCHEMA
)

ALL_TOOL_SCHEMAS = [
    WRITE_FILE_SCHEMA,
    READ_FILE_SCHEMA,
    CREATE_FOLDER_SCHEMA,
    RUN_CODE_SCHEMA,
    LAUNCH_APP_SCHEMA,
    CLOSE_APP_SCHEMA,
    DELETE_PATH_SCHEMA
]

TOOL_REGISTRY = {
    "write_file": write_file,
    "read_file": read_file,
    "create_folder": create_folder,
    "run_code": run_code,
    "launch_app": launch_app,
    "close_app": close_app,
    "delete_path": delete_path
}