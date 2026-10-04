import asyncio
import copy
import logging
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

from ev.events import EventBus
from ev.permissions import Permission
from ev.tools import ToolContext, ToolRegistry, ValidationError
from ev.tools.archives import register_archive_tools
from ev.tools.base import validate_schema
from ev.tools.results import evaluate_result


class ConstantSchemaTests(unittest.TestCase):
    def test_constant_mismatches_and_bool_number_equivalence_are_refused(self):
        for schema,value in (({'type':'boolean','const':False},True),({'const':True},1),
                             ({'const':1},True),({'const':'fixed'},'other'),
                             ({'const':None},False),({'enum':[True]},1),
                             ({'enum':[1]},True),({'const':[True,{'count':2}]},[1,{'count':2}]),
                             ({'const':{'flags':[False]}},{'flags':[0]})):
            with self.subTest(schema=schema,value=value):
                with self.assertRaises(ValidationError):validate_schema(value,schema)

    def test_json_numeric_equivalence_and_nested_constants_still_work(self):
        for schema,value in (({'const':1},1.0),({'enum':[1.0]},1),
                             ({'const':{'flags':[False,None],'count':2}},{'count':2.0,'flags':[False,None]}),
                             ({'type':'boolean','const':False},False)):
            self.assertEqual(validate_schema(value,schema),value)


class ArchiveContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.archive=self.root/'owned.zip'
        self.destination=self.root/'extracted'
        with zipfile.ZipFile(self.archive,'w') as archive:
            archive.writestr('a.txt',b'owned data')
            archive.writestr('empty/',b'')
        self.context=ToolContext({'security':{'allowed_roots':[str(self.root)],'max_tool_output_bytes':65536}},
                                 EventBus(),logging.getLogger('archive-contracts'))
        self.registry=ToolRegistry(self.context)
        register_archive_tools(self.registry)
        self.args={'path':str(self.archive),'destination':str(self.destination)}

    async def asyncTearDown(self):self.temporary.cleanup()

    async def test_actual_inspection_and_extraction_have_distinct_evidence(self):
        inspected=await self.registry.execute(self.registry.get('files.archive_inspect'),{'path':str(self.archive)})
        observation=evaluate_result('files.archive_inspect',inspected,read_only=True)
        self.assertTrue(observation.verified)
        self.assertFalse(observation.changed_state)
        self.assertEqual(observation.scope,'archive_metadata_only')
        self.assertFalse(inspected['payload_verified'])
        self.assertFalse(self.destination.exists())
        extracted=await self.registry.execute(self.registry.get('files.archive_extract'),self.args)
        publication=evaluate_result('files.archive_extract',extracted)
        self.assertTrue(publication.verified)
        self.assertTrue(publication.changed_state)
        self.assertEqual(publication.scope,'staged_archive_publication')
        self.assertEqual((self.destination/'a.txt').read_bytes(),b'owned data')
        for name,permission in (('files.archive_inspect',Permission.SAFE),('files.archive_extract',Permission.LOW_RISK)):
            spec=self.registry.get(name)
            self.assertEqual(spec.public()['contract_gaps'],[])
            self.assertTrue(spec.offline_available)
            self.assertFalse(spec.reversible)
            self.assertEqual(spec.permission,permission)

    async def test_large_preview_does_not_claim_payload_verification_or_fail_as_truncated(self):
        with zipfile.ZipFile(self.archive,'w') as archive:
            for index in range(51):archive.writestr(f'{index}.txt',b'x')
        result=await self.registry.execute(self.registry.get('files.archive_inspect'),{'path':str(self.archive)})
        self.assertEqual(len(result['entries']),50)
        self.assertTrue(result['listing_limited'])
        self.assertFalse(result['payload_verified'])
        receipt=evaluate_result('files.archive_inspect',result,read_only=True)
        self.assertTrue(receipt.ok)
        self.assertEqual(receipt.scope,'archive_metadata_only')

    async def test_contradictory_flags_and_malformed_outputs_are_rejected_before_return(self):
        cases=(('files.archive_inspect',{'path':str(self.archive)},'payload_verified',True),
               ('files.archive_inspect',{'path':str(self.archive)},'entries_count',True),
               ('files.archive_inspect',{'path':str(self.archive)},'expanded_bytes',-1),
               ('files.archive_extract',self.args,'executable_permissions_preserved',True))
        for name,args,key,value in cases:
            with self.subTest(name=name,key=key):
                spec=self.registry.get(name)
                original=spec.executor
                data=await original(args,self.context)
                data[key]=value
                spec.executor=AsyncMock(return_value=data)
                try:
                    with self.assertRaises(ValidationError):await self.registry.execute(self.registry.get(name),args)
                    self.assertFalse(evaluate_result(name,data,read_only=spec.read_only).verified)
                finally:spec.executor=original
        self.assertEqual((self.destination/'a.txt').read_bytes(),b'owned data')

    async def test_unproven_archive_results_never_get_generic_success_receipts(self):
        for name in ('files.archive_inspect','files.archive_extract'):
            for payload in ({'verified':True},{'payload_verified':False},{'verified':False}):
                receipt=evaluate_result(name,payload,read_only=name.endswith('inspect'))
                self.assertFalse(receipt.ok)
                self.assertFalse(receipt.verified)
                self.assertFalse(receipt.retryable)
                self.assertEqual(receipt.changed_state,False if name.endswith('inspect') else None)

    async def test_preview_counter_and_publication_counter_disagreements_fail(self):
        inspected=await self.registry.execute(self.registry.get('files.archive_inspect'),{'path':str(self.archive)})
        extracted=await self.registry.execute(self.registry.get('files.archive_extract'),self.args)
        variants=((inspected,'entries_count',3),(inspected,'listing_limited',True),
                  (inspected,'expanded_bytes',1),(extracted,'files_verified',3),
                  (extracted,'files_verified',0))
        for original,key,value in variants:
            data=copy.deepcopy(original);data[key]=value
            name='files.archive_inspect' if 'entries' in data else 'files.archive_extract'
            self.assertFalse(evaluate_result(name,data).verified)

    async def test_existing_destination_and_bad_crc_refuse_without_publication_or_replay(self):
        self.destination.mkdir()
        (self.destination/'keep.txt').write_text('later data')
        with self.assertRaises(ValidationError):await self.registry.execute(self.registry.get('files.archive_extract'),self.args)
        self.assertEqual((self.destination/'keep.txt').read_text(),'later data')
        self.args['destination']=str(self.root/'crc-output')
        data=self.archive.read_bytes();self.archive.write_bytes(data.replace(b'owned data',b'wrong data',1))
        inspected=await self.registry.execute(self.registry.get('files.archive_inspect'),{'path':str(self.archive)})
        self.assertFalse(inspected['payload_verified'])
        with self.assertRaises(zipfile.BadZipFile):await self.registry.execute(self.registry.get('files.archive_extract'),self.args)
        self.assertFalse(Path(self.args['destination']).exists())
        self.assertEqual(list(self.root.glob('.ev-extract-*')),[])


if __name__=='__main__':unittest.main()
