"""Explicit offline mock example; this synthetic response is not a live result."""
from tests.support.plumbing_inspection import plumbing_provider
import json
from laya.predict import predict

QUESTION = {'fruit': {'type': 'choice', 'instructions': 'Which fruit is mentioned?', 'criteria': {'apple': 'apple', 'pear': 'pear'}}}
RAW = {'answers': {'fruit': {'type': 'choice', 'choice': 'apple', 'probabilities': {'apple': 0.9, 'pear': 0.1}, 'answer_confidence': 0.9}}}

class MockAgent:
    def predict(self, text, questions):
        assert text == 'I ate an apple.' and questions == QUESTION
        return RAW

if __name__ == '__main__':
    print(json.dumps(predict('I ate an apple.', QUESTION, inspection_provider=plumbing_provider, loader=lambda model: MockAgent())))
