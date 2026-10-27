from dataclasses import replace
from unittest.mock import patch
import unittest

from project_compass import evidence_linking as links
from project_compass.fact_engine import derive_facts
from project_compass.models import FactKind
from project_compass.sample_data import sample_events


class CountedText(str):
    calls=0
    def casefold(self):
        type(self).calls+=1
        return super().casefold()


class EvidenceReadCostTests(unittest.TestCase):
    def setUp(self):
        self.source=sample_events()[0]
        self.fact=derive_facts([self.source])[0]

    def test_unmatched_large_catalog_prepares_each_text_and_reference_once_per_read(self):
        events=[replace(self.source,id=f'e{i}',kind='assistant_message',content=CountedText('Long unrelated material '*100),metadata={}) for i in range(40)]
        facts=[replace(self.fact,id=f'f{i}',value=CountedText(f'unmatched-needle-{i}'),evidence_ids=(),session_id='selected') for i in range(30)]
        CountedText.calls=0
        with patch.object(links,'_explicit_ids',wraps=links._explicit_ids) as explicit:
            self.assertEqual(links._eligible_pairs(events,facts),[])
        with self.subTest(cost='text'):self.assertLessEqual(CountedText.calls,len(events)+len(facts))
        with self.subTest(cost='metadata'):self.assertLessEqual(explicit.call_count,len(events))
        with patch.object(links,'_explicit_ids',side_effect=AssertionError('empty candidate set needs no preparation')):
            self.assertEqual(links._eligible_pairs(events,[]),[])

    def test_preparation_preserves_order_duplicate_sources_session_ambiguity_and_unicode(self):
        source=lambda ident,session,text,meta=None:replace(self.source,id=ident,kind='assistant_message',author='alice',session_id=session,content=text,metadata=meta or {})
        events=[source('source-a','sA','first source'),replace(source('result-a','sA','unrelated test result'),kind='test_result'),replace(source('result-b','sB','NeedleA passed'),kind='test_result'),source('explicit','other','explicit only',{'fact_refs':['C']}),source('unicode','other','STRASSE is visible'),source('source-a','sB','last source')]
        facts=[replace(self.fact,id='A',kind=FactKind.TASK,value='NeedleA',author='alice',evidence_ids=('missing','source-a'),session_id=None),replace(self.fact,id='B',kind=FactKind.TASK,value='NeedleB',author='alice',evidence_ids=(),session_id='sA'),replace(self.fact,id='C',kind=FactKind.TASK,value='NeedleC',author='alice',evidence_ids=(),session_id='sB'),replace(self.fact,id='D',kind=FactKind.DECISION,value='Straße',author='bob',evidence_ids=(),session_id='other')]
        rows=lambda values:[(f.id,e.id,e.content,f.session_id) for f,e in links._eligible_pairs(values,facts)]
        expected=[('A','source-a','first source','sB'),('A','result-b','NeedleA passed','sB'),('A','source-a','last source','sB'),('B','result-a','unrelated test result','sA'),('C','explicit','explicit only','sB'),('D','unicode','STRASSE is visible','other')]
        self.assertEqual(rows(events),expected)
        changed=[replace(e,content='replacement',metadata={}) if e.id in {'explicit','unicode'} else e for e in events]
        self.assertEqual(rows(changed),expected[:4])
        self.assertEqual(rows(events),expected)


if __name__=='__main__':unittest.main()
