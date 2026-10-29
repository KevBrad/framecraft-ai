"""Explicit local-only ASR profiles; optional models never trigger cloud inference."""
import os
from pathlib import Path

BALANCED_MODEL=os.environ.get('FRAMECRAFT_BALANCED_MODEL',os.environ.get('WHISPER_MODEL','tiny.en'))

def model_available(name):
    cache=Path(os.environ.get('MODEL_CACHE','data/models'))
    return any((cache/('models--Systran--faster-whisper-'+name)/'snapshots').glob('*/model.bin'))

def configure_profile(mode,report):
    # Respect an operator's explicit filesystem model configuration.
    if os.environ.get('WHISPER_MODEL_PATH'):
        return {'requested':mode,'model':'operator configured local model','device':'cpu','compute':'int8','fallback':False}
    balanced=BALANCED_MODEL
    chosen='base' if mode=='quality' else balanced
    fallback=mode=='quality' and not model_available('base')
    if fallback:
        chosen=balanced
        report('Quality model is not cached; using the installed local balanced model')
    os.environ['WHISPER_MODEL']=chosen
    os.environ['WHISPER_BEAM_SIZE']='5' if mode=='quality' and not fallback else '3'
    return {'requested':mode,'model':chosen,'device':'cpu','compute':'int8','beam_size':int(os.environ['WHISPER_BEAM_SIZE']),
            'fallback':fallback,'word_alignment':'Whisper word estimates, not forced alignment','remote_inference':False}
