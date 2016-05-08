import tempfile
db_file = tempfile.NamedTemporaryFile()


class Config(object):
	SECRET_KEY = 'secret key'
	FUZZER_KEY = 'secret key'
	DATA_DIRECTORY = 'data'
	UPLOAD_FREQUENCY = 60 * 5  # 5 minutes
	DOWNLOAD_FREQUENCY = 60 * 5  # 5 minutes

class ProdConfig(Config):
	ENV = 'prod'
	SQLALCHEMY_DATABASE_URI = 'sqlite:///../database.db'

	CACHE_TYPE = 'simple'


class DevConfig(Config):
	ENV = 'dev'
	FUZZER_KEY = ''
	DEBUG = True
	DEBUG_TB_INTERCEPT_REDIRECTS = False

	SQLALCHEMY_DATABASE_URI = 'sqlite:///../database.db'

	CACHE_TYPE = 'null'
	ASSETS_DEBUG = True


class TestConfig(Config):
	ENV = 'test'
	DEBUG = True
	DEBUG_TB_INTERCEPT_REDIRECTS = False

	SQLALCHEMY_DATABASE_URI = 'sqlite:///' + db_file.name
	SQLALCHEMY_ECHO = True

	CACHE_TYPE = 'null'
	WTF_CSRF_ENABLED = False
