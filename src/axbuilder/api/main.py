"""uvicorn 실행용: uvicorn axbuilder.api.main:app

설정은 환경변수(AXB_*)에서 읽는다. 테스트는 이 모듈을 import하지 않는다(실제 LLM·모델을 쓰므로).
"""

from axbuilder.api.app import create_app
from axbuilder.settings import Settings

app = create_app(Settings.from_env())
