import sys
from pathlib import Path
import json
from unittest.mock import MagicMock, patch

# Setup paths to import our modules
current_dir = Path(__file__).parent
sys.path.append(str(current_dir.parent / "scripts"))

# Mocking the load_llm_config to return a dummy config
with patch('llm_tagger.load_llm_config') as mock_load:
    mock_load.return_value = {
        'llm': {
            'enabled': True,
            'api_key': 'test-key',
            'base_url': 'https://api.test.com',
            'model': 'default-model'
        }
    }
    
    from llm_tagger import _call_llm_api

    def test_model(model_name):
        print(f"\nTesting model: {model_name}")
        messages = [{"role": "user", "content": "Hello"}]
        cfg = {
            'llm': {
                'provider': model_name,
                'api_key': 'test-key',
                'base_url': 'https://api.test.com',
                'model': 'default-model'
            }
        }
        
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                'choices': [{'message': {'content': 'Success'}}]
            }
            
            _call_llm_api(messages, cfg)
            
            # Check the payload sent to requests.post
            args, kwargs = mock_post.call_args
            payload = kwargs['json']
            print(f"Payload model: {payload.get('model')}")
            print(f"Payload keys: {list(payload.keys())}")
            
    # Test cases
    test_model('deepseek')
    test_model('qwen3.5-plus')
    test_model('glm-5')
    test_model('kimi-k2.5')
