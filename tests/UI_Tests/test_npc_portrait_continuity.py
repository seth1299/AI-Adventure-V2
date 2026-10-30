from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from PIL import Image
from PySide6.QtWidgets import QApplication

from ai_adventure.application.asset_generation_service import AssetGenerationService
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.workers.visual_assets import _VisualAssetCoordinator
from ai_adventure.visual_assets import save_relative_image_filename
from tests.qt_test_case import QtTestCase


class NpcPortraitContinuityTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'saves').mkdir()
        self.repo = SaveRepository.create_new_save(self.root / 'saves', 'Portrait continuity')
        self.repo.upsert_npc(npc_id='npc_vanja', name='Vanja', display_name='Archivist Vanja',
                             location='Library', public_description='A courteous archivist.')
        self.images = self.root / 'images'
        self.coordinator = _VisualAssetCoordinator(images_dir=self.images,
                                                   api_key_path=self.root / 'unused-key', enabled=True)
        self.coordinator._start_next = Mock()  # No provider calls or threads in these tests.
        self.addCleanup(self.coordinator.deleteLater)
        key_patch = patch('ai_adventure.ui.workers.visual_assets.read_api_key', return_value='test-key')
        key_patch.start()
        self.addCleanup(key_patch.stop)

    def request(self):
        return next(request for request in AssetGenerationService.requests_for(self.repo)
                    if request.subject_type == 'npc')

    def register(self, request, ready=False):
        filename = save_relative_image_filename(self.repo, request)
        if ready:
            path = self.images / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (32, 32), (30, 60, 90)).save(path)
        self.repo.ensure_visual_asset(asset_id=request.asset_id, subject_type='npc',
                                      subject_key=request.subject_key, display_name=request.display_name,
                                      descriptor_hash=request.descriptor_hash, filename=filename,
                                      prompt=request.prompt, model='gemini-3.1-flash-lite-image',
                                      image_style=request.image_style, visual_description=request.description,
                                      ready=ready)
        if ready:
            # Keep fixture timestamps distinct without sleeping.
            with self.repo.transaction() as connection:
                connection.execute('UPDATE visual_assets SET updated_at=? WHERE asset_id=?',
                                   ('2000-01-01T00:00:00', request.asset_id))

    def scan(self, request):
        with (patch.object(AssetGenerationService, 'requests_for', return_value=[request]),
              patch.object(AssetGenerationService, 'find_local_reuse', return_value=None)):
            self.coordinator.scan(self.repo)

    def change_profile(self):
        self.repo.upsert_npc(npc_id='npc_vanja', name='Vanja', display_name='Vanja Reed',
                             public_description='A bespectacled woman in neat scholarly robes.')
        return replace(self.request(), message_ids=('conversation-followup',))

    def test_profile_rename_description_and_style_changes_keep_ready_portrait(self):
        original = self.request()
        self.register(original, ready=True)
        changed = replace(self.change_profile(), image_style='oil_painting')
        self.assertNotEqual(original.asset_id, changed.asset_id)
        self.scan(changed)
        self.assertEqual(self.coordinator._queue, [])
        self.assertIsNone(self.repo.get_visual_asset_by_id(changed.asset_id))
        self.assertEqual(self.repo.get_visual_asset('npc', 'npc_vanja')['asset_id'], original.asset_id)
        self.assertEqual([asset['asset_id'] for asset in self.repo.list_visual_assets_for_message('conversation-followup')],
                         [original.asset_id])

    def test_description_change_while_first_portrait_is_queued_does_not_queue_second(self):
        original = self.request()
        self.scan(original)
        self.assertEqual(len(self.coordinator._queue), 1)
        changed = self.change_profile()
        self.scan(changed)
        self.assertEqual([entry[1].asset_id for entry in self.coordinator._queue], [original.asset_id])
        self.assertIsNone(self.repo.get_visual_asset_by_id(changed.asset_id))

    def test_description_change_while_first_portrait_is_generating_does_not_queue_second(self):
        original = self.request()
        self.register(original)
        self.repo.set_visual_asset_status(original.asset_id, 'generating')
        self.coordinator._active_subject = (str(self.repo.db_path), 'npc', 'npc_vanja')
        self.scan(self.change_profile())
        self.assertEqual(self.coordinator._queue, [])

    def test_missing_image_file_can_be_recovered(self):
        original = self.request()
        self.register(original, ready=True)
        (self.images / save_relative_image_filename(self.repo, original)).unlink()
        changed = self.change_profile()
        self.scan(changed)
        self.assertEqual([entry[1].asset_id for entry in self.coordinator._queue], [changed.asset_id])

    def test_explicit_generation_can_replace_existing_portrait(self):
        original = self.request()
        self.register(original, ready=True)
        changed = self.change_profile()
        self.register(changed)
        success, _ = self.coordinator.create_initial_image(self.repo, changed)
        self.assertTrue(success)
        self.assertEqual([entry[1].asset_id for entry in self.coordinator._queue], [changed.asset_id])
        data = BytesIO()
        image = Image.new('RGB', (64, 64), (255, 0, 255))
        image.paste((80, 110, 140), (16, 10, 48, 60))
        image.save(data, format='PNG')
        self.coordinator._handle_completed(self.repo, changed, data.getvalue(), 'image/png')
        self.assertEqual(self.repo.get_visual_asset('npc', 'npc_vanja')['asset_id'], changed.asset_id)

    def test_explicit_upload_can_replace_existing_portrait(self):
        original = self.request()
        self.register(original, ready=True)
        changed = self.change_profile()
        self.register(changed)
        source = self.root / 'replacement.png'
        Image.new('RGB', (32, 32), (80, 110, 140)).save(source)
        success, _ = self.coordinator.upload_initial_image(self.repo, changed, source)
        self.assertTrue(success)
        self.assertEqual(self.repo.get_visual_asset('npc', 'npc_vanja')['asset_id'], changed.asset_id)
        self.scan(changed)
        self.assertEqual(self.coordinator._queue, [])

    def test_explicit_retry_of_failed_replacement_is_not_blocked_by_old_portrait(self):
        original = self.request()
        self.register(original, ready=True)
        changed = self.change_profile()
        self.register(changed)
        self.repo.set_visual_asset_status(changed.asset_id, 'generating')
        self.repo.set_visual_asset_status(changed.asset_id, 'failed', error_message='Temporary failure')
        self.assertEqual(self.repo.reset_failed_visual_assets(), 1)
        self.scan(changed)
        self.assertEqual([entry[1].asset_id for entry in self.coordinator._queue], [changed.asset_id])
