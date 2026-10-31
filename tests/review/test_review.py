import csv
import tempfile
import unittest
from pathlib import Path
from mavc.review import export_sheet, import_sheet
from mavc.learn import Candidate, Example, admit, check_splits, export, split, Admission, Decision


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'sheet.csv'
        self.rows = [{'id':'a','text':'café\nexact quote','teacher':'X','answer':'Y'},
                     {'id':'b','text':'other','teacher':'Y','answer':''}]

    def edit(self, edit):
        with self.path.open(newline='') as stream:
            reader = csv.DictReader(stream); fields = reader.fieldnames; rows = list(reader)
        edit(rows)
        with self.path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fields); writer.writeheader(); writer.writerows(rows)

    def load(self, observer='model'):
        return import_sheet(self.path, self.rows, observer_type=observer, allowed_labels={'X','Y'})

    def test_roundtrip_preserves_disagreement_and_text(self):
        export_sheet(self.rows, self.path)
        self.edit(lambda rows:rows[1].update(answer='X',finding_code='label_disagreement',disposition='correct'))
        for observer in ['model','human']:
            result = self.load(observer)
            self.assertEqual(result['missing_ids'], [])
            self.assertEqual(result['records'][0]['row']['text'], self.rows[0]['text'])
            self.assertEqual(result['records'][0]['row']['teacher'], 'X')
            self.assertEqual(result['records'][0]['row']['answer'], 'Y')
            self.assertTrue(result['records'][0]['metadata']['test_only'])
        with self.assertRaises(FileExistsError):export_sheet(self.rows, self.path)

    def test_completed_answer_and_evidence_cannot_change(self):
        for field in ['answer','text','teacher']:
            self.path.unlink(missing_ok=True); export_sheet(self.rows,self.path)
            self.edit(lambda rows:rows[0].update({field:'X' if field=='answer' else 'changed'}))
            with self.assertRaisesRegex(ValueError,'cannot overwrite'):self.load()

    def test_subsequent_import_can_edit_populated_review_notes(self):
        export_sheet(self.rows,self.path)
        self.edit(lambda rows:rows[0].update(finding_code='missing_context',
                  disposition='unresolved',review_note='Check context'))
        self.rows = [record['row'] for record in self.load()['records']]
        self.edit(lambda rows:rows[0].update(finding_code='ok',disposition='accept',
                  review_note='Context checked'))
        updated = self.load()['records'][0]['row']
        self.assertEqual((updated['finding_code'],updated['disposition'],updated['review_note']),
                         ('ok','accept','Context checked'))
        for field in ['id','text','teacher','answer']:
            self.assertEqual(updated[field],self.rows[0][field])
        self.edit(lambda rows:rows[0].update(answer='X'))
        with self.assertRaisesRegex(ValueError,'cannot overwrite'):self.load()

    def test_missing_duplicate_invalid_ids_and_invalid_decisions(self):
        edits = [(lambda rows:rows.pop(), None),
                 (lambda rows:rows[1].update(id='a'), 'duplicate ID'),
                 (lambda rows:rows[1].update(id=''), 'invalid id'),
                 (lambda rows:rows[1].update(id='unknown'), 'invalid IDs'),
                 (lambda rows:rows[1].update(answer='Z'), 'invalid label'),
                 (lambda rows:rows[1].update(disposition='approved'), 'invalid disposition')]
        for edit, error in edits:
            self.path.unlink(missing_ok=True); export_sheet(self.rows,self.path);self.edit(edit)
            if error:
                with self.assertRaisesRegex(ValueError,error):self.load()
            else:self.assertEqual(self.load()['missing_ids'],['b'])

    def test_imported_labels_cannot_be_projected_into_training(self):
        export_sheet(self.rows,self.path)
        for observer in ['model','human']:
            record = self.load(observer)['records'][0]
            candidate = Candidate(record['id'],'completed',metadata=record['metadata'])
            decision = list(admit([candidate],lambda c:Example('text','Y',{
                'label_source':'teacher','test_only':False,'split':'train'})))[0]
            self.assertEqual(decision.decision.status,'quarantined')
            self.assertIsNone(decision.example)

    def test_review_split_assignment_and_export_stay_test_only(self):
        for number, marker in enumerate([{'label_source':'review'}, {'label_source':'model_review'}, {'test_only':True}]):
            meta = {'document_id':'doc','pdf_sha256':'pdf','split':'train',**marker}
            example = Example('text','answer',meta)
            self.assertEqual(split([example], fractions={'train':1})[0].metadata['split'],'test')
            with self.assertRaisesRegex(ValueError,'test-only'):check_splits([meta])
            with self.assertRaisesRegex(ValueError,'test-only'):
                export([Admission('x',Decision(),meta,example)],Path(self.tmp.name)/f'export-{number}')
