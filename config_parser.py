from configparser import ConfigParser
import os


def parse_config(filename, section='postgresql'):
    # create a parser
    parser = ConfigParser()
    # read config file
    parser.read(filename)

    # get section, default to postgresql
    configs = {}
    if parser.has_section(section):
        params = parser.items(section)
        for param in params:
            configs[param[0]] = param[1]
    else:
        raise Exception('Section {0} not found in the {1} file'.format(section, filename))
    return configs


def get_conn_str():
    """
    Get database related configurations
    """
    config = os.environ.get('DB_CONFIG', 'database.ini')
    db_info = parse_config(config)
    hostname = db_info.get("host")
    database = db_info.get("database")
    username = db_info.get("user")
    password = db_info.get("password")
    port = db_info.get("port")
    conn_str = f"postgresql://{username}:{password}@{hostname}:{port}/{database}"
    return conn_str


def security_API_key():
    config = os.environ.get('DB_CONFIG', 'database.ini')
    security_info = parse_info(config, 'security')
    apikey = security_info.get("apikey")
    return apikey


def get_api_configs():
    """
    Get API related configurations
    """
    config = os.environ.get('DB_CONFIG', 'database.ini')
    api_configs = parse_config(config, "api")
    return api_configs