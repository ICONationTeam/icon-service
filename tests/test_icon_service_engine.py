# -*- coding: utf-8 -*-

# Copyright 2017-2018 theloop Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""IconScoreEngine testcase
"""

import unittest
from unittest.mock import Mock

from iconservice.base.address import AddressPrefix, ICX_ENGINE_ADDRESS
from iconservice.base.block import Block
from iconservice.base.exception import ExceptionCode, ServerErrorException
from iconservice.base.message import Message
from iconservice.base.transaction import Transaction
from iconservice.database.batch import BlockBatch, TransactionBatch
from iconservice.icon_service_engine import IconServiceEngine
from iconservice.iconscore.icon_score_context import IconScoreContext
from iconservice.iconscore.icon_score_context import IconScoreContextFactory
from iconservice.iconscore.icon_score_context import IconScoreContextType
from iconservice.iconscore.icon_score_result import TransactionResult
from iconservice.iconscore.icon_score_step import IconScoreStepCounterFactory, \
    StepType
from iconservice.utils import sha3_256
from tests import create_block_hash, create_address, rmtree, create_tx_hash

context_factory = IconScoreContextFactory(max_size=1)


def _create_context(context_type: IconScoreContextType) -> IconScoreContext:
    context = context_factory.create(context_type)

    if context.type == IconScoreContextType.INVOKE:
        context.block_batch = BlockBatch()
        context.tx_batch = TransactionBatch()

    return context


class TestIconServiceEngine(unittest.TestCase):
    def setUp(self):
        self._state_db_root_path = '.db'
        self._icon_score_root_path = '.score'

        rmtree(self._icon_score_root_path)
        rmtree(self._state_db_root_path)

        engine = IconServiceEngine()
        engine.open(icon_score_root_path=self._icon_score_root_path,
                    state_db_root_path=self._state_db_root_path)
        self._engine = engine

        self._genesis_address = create_address(
            AddressPrefix.EOA, b'genesis')
        self._treasury_address = create_address(
            AddressPrefix.EOA, b'treasury')

        self._tx_hash = create_tx_hash(b'tx')
        self._from = self._genesis_address
        self._to = create_address(AddressPrefix.EOA, b'to')
        self._icon_score_address = create_address(
            AddressPrefix.CONTRACT, b'score')
        self._total_supply = 100 * 10 ** 18

        self._step_counter_factory = IconScoreStepCounterFactory()
        self._step_counter_factory.set_step_unit(StepType.TRANSACTION, 10)
        self._step_counter_factory.set_step_unit(StepType.STORAGE_SET, 10)
        self._step_counter_factory.set_step_unit(StepType.STORAGE_REPLACE, 10)
        self._step_counter_factory.set_step_unit(StepType.STORAGE_DELETE, 10)
        self._step_counter_factory.set_step_unit(StepType.TRANSFER, 10)
        self._step_counter_factory.set_step_unit(StepType.CALL, 10)
        self._step_counter_factory.set_step_unit(StepType.EVENTLOG, 10)

        self._engine._step_counter_factory = self._step_counter_factory
        self._engine._precommit_state = None

        accounts = [
            {
                'name': 'god',
                'address': self._genesis_address,
                'balance': self._total_supply
            },
            {
                'name': 'treasury',
                'address': self._treasury_address,
                'balance': 0
            }
        ]

        block = Block(0, create_block_hash(b'block'), 0, None)
        tx = {'method': '',
              'params': {'txHash': self._tx_hash},
              'genesisData': {'accounts': accounts}}
        tx_lists = [tx]

        self._engine.invoke(block, tx_lists)
        self._engine.commit()

    def tearDown(self):
        self._engine.close()
        rmtree(self._icon_score_root_path)
        rmtree(self._state_db_root_path)

    def test_query(self):
        method = 'icx_getBalance'
        params = {'address': self._from}

        balance = self._engine.query(method, params)
        self.assertTrue(isinstance(balance, int))
        self.assertEqual(self._total_supply, balance)

    def test_call_in_query(self):
        context = context_factory.create(IconScoreContextType.QUERY)

        method = 'icx_getBalance'
        params = {'address': self._from}

        balance = self._engine._call(context, method, params)
        self.assertTrue(isinstance(balance, int))
        self.assertEqual(self._total_supply, balance)

        context_factory.destroy(context)

    def test_call_in_invoke(self):
        context = _create_context(IconScoreContextType.INVOKE)

        _from = self._genesis_address
        _to = self._to
        value = 1 * 10 ** 18

        method = 'icx_sendTransaction'
        params = {
            'from': _from,
            'to': _to,
            'value': value,
            'fee': 10 ** 16,
            'timestamp': 1234567890,
            'txHash': self._tx_hash
        }

        context.tx = Transaction(tx_hash=params['txHash'],
                                 index=0,
                                 origin=_from,
                                 timestamp=params['timestamp'],
                                 nonce=params.get('nonce', None))

        self._engine._call(context, method, params)

        tx_batch = context.tx_batch
        self.assertEqual(1, len(tx_batch))
        self.assertTrue(ICX_ENGINE_ADDRESS in tx_batch)

        icon_score_batch = tx_batch[ICX_ENGINE_ADDRESS]
        self.assertEqual(2, len(icon_score_batch))

        balance = int.from_bytes(
            icon_score_batch[_to.body][-32:], 'big')
        self.assertEqual(value, balance)

        balance = int.from_bytes(
            icon_score_batch[_from.body][-32:], 'big')
        self.assertEqual(self._total_supply - value, balance)

        context_factory.destroy(context)

    def test_invoke(self):
        block_height = 1
        block_hash = create_block_hash(b'block')
        block_timestamp = 0
        value = 1 * 10 ** 18

        tx = {
            'method': 'icx_sendTransaction',
            'params': {
                'from': self._genesis_address,
                'to': self._to,
                'value': value,
                'fee': 10 ** 16,
                'timestamp': 1234567890,
                'txHash': create_tx_hash(b'txHash'),
            }
        }

        block = Block(block_height, block_hash, block_timestamp, create_block_hash(b'prev'))

        tx_results, _ = self._engine.invoke(block, [tx])
        print(tx_results[0])

    def test_score_invoke_failure(self):
        method = 'icx_sendTransaction'
        params = {
            'from': self._from,
            'to': self._icon_score_address,
            'value': 1 * 10 ** 18,
            'fee': 10 ** 16,
            'timestamp': 1234567890,
            'txHash': self._tx_hash,
            'dataType': 'call',
            'data': {
                'method': 'transfer',
                'params': {
                    'to': self._to,
                    'value': 777
                }
            }
        }

        context = _create_context(IconScoreContextType.INVOKE)
        context.tx = Transaction(tx_hash=params['txHash'],
                                 origin=params['from'],
                                 index=0,
                                 timestamp=params['timestamp'],
                                 nonce=params.get('nonce', None))
        context.msg = Message(sender=params['from'], value=params['value'])
        context.traces = Mock(spec=list)

        tx_result = self._engine._call(context, method, params)
        self.assertTrue(isinstance(tx_result, TransactionResult))
        self.assertEqual(TransactionResult.FAILURE, tx_result.status)
        self.assertEqual(self._icon_score_address, tx_result.to)
        self.assertEqual(self._tx_hash, tx_result.tx_hash)
        self.assertIsNone(tx_result.score_address)
        context.traces.append.assert_called()
        print(tx_result)

        context_factory.destroy(context)

    def test_commit(self):
        with self.assertRaises(ServerErrorException) as cm:
            self._engine.commit()
        e = cm.exception
        self.assertEqual(ExceptionCode.SERVER_ERROR, e.code)
        self.assertEqual('Precommit state is none on commit', e.message)

    def test_rollback(self):
        self._engine.rollback()
        self.assertIsNone(self._engine._precommit_state)
        self.assertEqual(
            0, len(self._engine._icon_score_deploy_engine._deferred_tasks))
        

if __name__ == '__main__':
    unittest.main()
