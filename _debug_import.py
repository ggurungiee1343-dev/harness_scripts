import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath('.')))
sys.path.insert(0, os.path.abspath('.'))
import ingest_engine
print('IMPORTED FROM:', ingest_engine.__file__)
# TagLinker 호출 시도
from ingest_engine import IngestEngine
print('TagLinker call OK')
