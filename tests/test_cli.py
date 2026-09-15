#
# Copyright The NOMAD Authors.
#
# This file is part of NOMAD. See https://nomad-lab.eu for further info.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import asyncio
import datetime
import json
import os
import tempfile
import time

import click.testing
import pytest

from nomad import files
from nomad import processing as proc
from nomad.auth.pat import PATPruneResult, pat_service
from nomad.cli import cli
from nomad.cli.cli import POPO
from nomad.common import now
from nomad.config import config
from nomad.processing import Entry, ProcessStatus, Upload
from nomad.search import search
from nomad.utils.exampledata import ExampleData

# TODO there is much more to test


def invoke_cli(*args, **kwargs):
    return click.testing.CliRunner().invoke(*args, obj=POPO(), **kwargs)


@pytest.fixture(autouse=True)
def fast_cli_elastic(monkeypatch):
    monkeypatch.setattr(
        'nomad.metainfo.elasticsearch_extension.create_indices',
        lambda *args, **kwargs: None,
    )


@pytest.mark.parametrize(
    'prefix, expected_location',
    [
        pytest.param('/', 'location /gui/', id='root'),
        pytest.param('/develop/', 'location /develop/gui/', id='trailing-slash'),
        pytest.param('develop', 'location /develop/gui/', id='explicit-prefix'),
    ],
)
def test_nginx_conf_normalizes_root_prefix(prefix, expected_location, capsys):
    from nomad.cli.admin.admin import nginx_conf

    nginx_conf.callback(prefix, 'backend', 8000, False)
    output = capsys.readouterr().out

    assert expected_location in output
    assert '//gui' not in output


@pytest.fixture
def mock_prune_pat(monkeypatch):
    captured = {}

    def _install(result: PATPruneResult):
        def _mock(**kwargs):
            captured.clear()
            captured.update(kwargs)
            return result

        monkeypatch.setattr(pat_service, 'prune', _mock)
        return captured

    return _install


@pytest.mark.usefixtures('reset_config', 'nomad_logging')
class TestCli:
    def test_help(self, example_mainfile):
        start = time.time()
        result = invoke_cli(cli, ['--help'], catch_exceptions=False)
        assert result.exit_code == 0
        assert time.time() - start < 1


@pytest.mark.usefixtures('reset_config', 'nomad_logging', 'nomad_parsers')
class TestParse:
    def test_parser(self, example_mainfile):
        _, mainfile_path = example_mainfile
        result = invoke_cli(cli, ['parse', mainfile_path], catch_exceptions=False)
        assert result.exit_code == 0

    def test_save_plot_dir(self):
        directory = 'tests/data/datamodel/metainfo/plotly'
        mainfile = 'plotly.schema.archive.yaml'
        mainfile_path = os.path.join(directory, mainfile)
        with tempfile.TemporaryDirectory() as tmpdirname:
            result = invoke_cli(
                cli,
                ['parse', mainfile_path, '--save-plot-dir', tmpdirname],
                catch_exceptions=False,
            )
            assert result.exit_code == 0

            files = os.listdir(tmpdirname)
            files.sort()
            assert len(files) == 4
            for i, file in enumerate(files):
                assert file == f'{mainfile}_{i}.png'


@pytest.mark.usefixtures(
    'reset_config', 'no_warn', 'mongo_infra', 'elastic_infra', 'raw_files_infra'
)
class TestAdmin:
    def test_reset(self, reset_infra):
        result = invoke_cli(
            cli, ['admin', 'reset', '--i-am-really-sure'], catch_exceptions=False
        )
        assert result.exit_code == 0
        from nomad.metainfo.elasticsearch_extension import entry_index

        entry_index.create_index(upsert=True)

    def test_reset_not_sure(self):
        result = invoke_cli(cli, ['admin', 'reset'], catch_exceptions=False)
        assert result.exit_code == 1

    # TODO this has somekind of raise condition in it and the test fails every other time
    # on the CI/CD
    # def test_clean(self, published):
    #     upload_id = published.upload_id

    #     Upload.objects(upload_id=upload_id).delete()
    #     assert published.upload_files.exists()
    #     assert Entry.objects(upload_id=upload_id).first() is not None
    #     search.refresh()
    #     assert search.SearchRequest().search_parameter('upload_id', upload_id).execute()['total'] > 0
    #     # TODO test new index pair
    #     # assert es_search(owner=None, query=dict(upload_id=upload_id)).pagination.total == 0
    #     # assert es_search(owner=None, query=dict(upload_id=upload_id)).pagination.total == 0

    #     result = invoke_cli(
    #         cli, ['admin', 'clean', '--force', '--skip-es'], catch_exceptions=False)

    #     assert result.exit_code == 0
    #     assert not published.upload_files.exists()
    #     assert Entry.objects(upload_id=upload_id).first() is None
    #     search.refresh()
    #     assert search.SearchRequest().search_parameter('upload_id', upload_id).execute()['total'] > 0
    #     # TODO test new index pair
    #     # assert es_search(owner=None, query=dict(upload_id=upload_id)).pagination.total == 0

    @pytest.mark.asyncio
    async def test_lift_embargo(self, elastic_function, published, temporal_worker):
        upload_id = published.upload_id
        entry = Entry.objects(upload_id=upload_id).first()

        assert published.upload_files.exists()
        assert published.with_embargo
        assert (
            search(owner='public', query=dict(upload_id=upload_id)).pagination.total
            == 0
        )

        async with temporal_worker():
            # 1. Unexpired embargo -> does not lift
            published.publish_time = now()
            published.save()
            result = await asyncio.to_thread(
                lambda: invoke_cli(
                    cli,
                    ['admin', 'lift-embargo'],
                    catch_exceptions=False,
                )
            )
            assert result.exit_code == 0
            await published.await_workflows()
            published.reload()
            assert published.with_embargo
            assert (
                search(owner='public', query=dict(upload_id=upload_id)).pagination.total
                == 0
            )

            # 2. Expired embargo with --dry -> does not lift
            published.publish_time = datetime.datetime(
                year=2012, month=1, day=1, tzinfo=datetime.timezone.utc
            )
            published.save()
            result = await asyncio.to_thread(
                lambda: invoke_cli(
                    cli,
                    ['admin', 'lift-embargo', '--dry'],
                    catch_exceptions=False,
                )
            )
            assert result.exit_code == 0
            await published.await_workflows()
            published.reload()
            assert published.with_embargo
            assert (
                search(owner='public', query=dict(upload_id=upload_id)).pagination.total
                == 0
            )

            # 3. Expired embargo live -> lifts embargo
            result = await asyncio.to_thread(
                lambda: invoke_cli(
                    cli,
                    ['admin', 'lift-embargo'],
                    catch_exceptions=False,
                )
            )
            assert result.exit_code == 0
            await published.await_workflows()
            published.reload()
            assert not published.with_embargo
            assert (
                search(owner='public', query=dict(upload_id=upload_id)).pagination.total
                > 0
            )
            with files.UploadFiles.get(upload_id=upload_id).read_archive(
                entry_id=entry.entry_id
            ) as archive:
                assert entry.entry_id in archive

    def test_delete_entry(self, elastic_function, published):
        upload_id = published.upload_id
        entry = Entry.objects(upload_id=upload_id).first()

        result = invoke_cli(
            cli,
            ['admin', 'entries', 'rm', '--', entry.entry_id],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert 'deleting' in result.stdout
        assert Upload.objects(upload_id=upload_id).first() is not None
        assert Entry.objects(entry_id=entry.entry_id).first() is None

    @pytest.mark.parametrize(
        'args, expected_call, expected_summary, expected_warning',
        [
            # Test `inactive_for`
            (
                [
                    'admin',
                    'pats',
                    'prune',
                    '--dry-run',
                    '--inactive-for',
                    '12h',
                    '--expired',
                    '--revoked',
                    '--user-id',
                    'u1',
                ],
                {
                    'dry_run': True,
                    'inactive_for': datetime.timedelta(hours=12),
                    'inactive_before': None,
                    'expired': True,
                    'revoked': True,
                    'user_id': 'u1',
                },
                {
                    'matched': 2,
                    'deleted': 0,
                    'expired_matched': 1,
                    'revoked_matched': 1,
                    'dry_run': True,
                },
                None,
            ),
            # Test `inactive_before`
            (
                [
                    'admin',
                    'pats',
                    'prune',
                    '--inactive-before',
                    '2026-04-21T09:30:00',
                    '--expired',
                    '--user-id',
                    'u2',
                ],
                {
                    'dry_run': False,
                    'inactive_for': None,
                    'inactive_before': datetime.datetime(2026, 4, 21, 9, 30, 0),
                    'expired': True,
                    'revoked': False,
                    'user_id': 'u2',
                },
                {
                    'matched': 1,
                    'deleted': 1,
                    'expired_matched': 1,
                    'revoked_matched': 0,
                    'dry_run': False,
                },
                None,
            ),
            # Test warning on missing unit
            (
                [
                    'admin',
                    'pats',
                    'prune',
                    '--dry-run',
                    '--inactive-for',
                    '90',
                    '--expired',
                    '--user-id',
                    'u3',
                ],
                {
                    'dry_run': True,
                    'inactive_for': datetime.timedelta(days=90),
                    'inactive_before': None,
                    'expired': True,
                    'revoked': False,
                    'user_id': 'u3',
                },
                {
                    'matched': 0,
                    'deleted': 0,
                    'expired_matched': 0,
                    'revoked_matched': 0,
                    'dry_run': True,
                },
                'Warning: No unit specified in duration "90", assuming "d".',
            ),
        ],
    )
    def test_prune_pats_success(
        self,
        monkeypatch,
        mock_prune_pat,
        args,
        expected_call,
        expected_summary,
        expected_warning,
    ):
        monkeypatch.setattr('nomad.infrastructure.setup_mongo', lambda: None)
        cutoff = now() - datetime.timedelta(days=7)
        captured = mock_prune_pat(
            PATPruneResult(
                matched=expected_summary['matched'],
                deleted=expected_summary['deleted'],
                expired_matched=expected_summary['expired_matched'],
                revoked_matched=expected_summary['revoked_matched'],
                cutoff=cutoff,
                dry_run=expected_summary['dry_run'],
                user_id=expected_call['user_id'],
            )
        )

        result = invoke_cli(cli, args, catch_exceptions=False)
        assert result.exit_code == 0
        assert captured == expected_call
        assert f'total_matched: {expected_summary["matched"]}' in result.stdout
        assert (
            f'expired_matched: {expected_summary["expired_matched"]}' in result.stdout
        )
        assert (
            f'revoked_matched: {expected_summary["revoked_matched"]}' in result.stdout
        )
        assert f'deleted: {expected_summary["deleted"]}' in result.stdout
        assert f'dry_run: {expected_summary["dry_run"]}' in result.stdout
        assert 'cutoff_local:' in result.stdout
        assert 'cutoff_utc:' in result.stdout
        if expected_warning:
            assert expected_warning in result.output
        else:
            assert 'Warning: No unit specified' not in result.output

    @pytest.mark.parametrize(
        'args, expected_message',
        [
            (
                [
                    'admin',
                    'pats',
                    'prune',
                    '--expired',
                    '--inactive-before',
                    '2026-04-21T09:30:00',
                    '--inactive-for',
                    '90d',
                ],
                'inactive_before and inactive_for are mutually exclusive.',
            ),
            (
                [
                    'admin',
                    'pats',
                    'prune',
                    '--expired',
                    '--inactive-for',
                    '-1d',
                ],
                'Duration must be non-negative.',
            ),
        ],
    )
    def test_prune_pats_invalid_arguments(self, monkeypatch, args, expected_message):
        monkeypatch.setattr('nomad.infrastructure.setup_mongo', lambda: None)
        result = invoke_cli(cli, args, catch_exceptions=False)
        assert result.exit_code != 0
        assert expected_message in result.output


def transform_for_index_test(entry):
    entry.comment = 'specific'
    return entry


@pytest.mark.usefixtures('reset_config', 'no_warn')
class TestAdminUploads:
    def test_query_mongo(self, user1, mongo_function):
        upload_id = 'test_upload_id'
        Upload.create(upload_id=upload_id, main_author=user1, upload_name='test_upload')
        Entry.create(upload_id=upload_id, entry_id='test_entry_id', mainfile='mainfile')

        query = dict(upload_id=upload_id)
        result = invoke_cli(
            cli,
            ['admin', 'uploads', '--entries-mongo-query', json.dumps(query), 'ls'],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert '1 uploads selected' in result.stdout

    def test_ls(self, user1, mongo_function):
        upload_id = 'test_upload_id'
        Upload.create(upload_id=upload_id, main_author=user1, upload_name='test_upload')

        result = invoke_cli(
            cli, ['admin', 'uploads', 'ls', upload_id], catch_exceptions=False
        )

        assert result.exit_code == 0
        assert '1 uploads selected' in result.stdout

    def test_ls_query(self, user1, mongo_function, elastic_function):
        upload_id = 'test_upload_id'
        data = ExampleData(main_author=user1)
        data.create_upload(upload_id=upload_id)
        data.create_entry(upload_id=upload_id)
        data.save(with_es=True, with_files=False)

        result = invoke_cli(
            cli,
            [
                'admin',
                'uploads',
                '--entries-es-query',
                f'{{"upload_id":"{upload_id}"}}',
                'ls',
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert '1 uploads selected' in result.stdout

    def test_ls_unindexed(self, elastic_function, published, user1):
        import nomad.search as nomad_search

        unindexed_upload_id = published.upload_id
        indexed_upload_id = 'indexed_upload'

        data = ExampleData(main_author=user1)
        data.create_upload(upload_id=indexed_upload_id)
        data.create_entry(upload_id=indexed_upload_id)
        data.save(with_es=True, with_files=False)

        nomad_search.delete_by_query(
            query={'upload_id': unindexed_upload_id},
            owner='admin',
            user_id=config.services.admin_user_id,
            refresh=True,
        )

        result = invoke_cli(
            cli,
            ['admin', 'uploads', '--unindexed', 'ls', '--ids'],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert unindexed_upload_id in result.stdout
        assert indexed_upload_id not in result.stdout

    def test_rm(self, elastic_function, published):
        upload_id = published.upload_id

        result = invoke_cli(
            cli, ['admin', 'uploads', 'rm', upload_id], catch_exceptions=False
        )

        assert result.exit_code == 0
        assert 'deleting' in result.stdout
        assert Upload.objects(upload_id=upload_id).first() is None
        assert Entry.objects(upload_id=upload_id).first() is None

    def test_index(self, elastic_function, published):
        upload_id = published.upload_id
        entry = Entry.objects(upload_id=upload_id).first()
        entry.comment = 'specific'
        entry.save()

        assert search(owner='all', query=dict(comment='specific')).pagination.total == 0

        result = invoke_cli(
            cli, ['admin', 'uploads', 'index', upload_id], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert 'index' in result.stdout

        assert search(owner='all', query=dict(comment='specific')).pagination.total == 1

    def test_index_with_transform(self, elastic_function, published):
        upload_id = published.upload_id
        assert search(owner='all', query=dict(comment='specific')).pagination.total == 0

        result = invoke_cli(
            cli,
            [
                'admin',
                'uploads',
                'index',
                '--transformer',
                'tests.test_cli.transform_for_index_test',
                upload_id,
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert 'index' in result.stdout

        assert search(owner='all', query=dict(comment='specific')).pagination.total == 1

    @pytest.mark.asyncio
    async def test_re_process(
        self, elastic_function, published, monkeypatch, temporal_worker
    ):
        monkeypatch.setattr('nomad.config.meta.version', 'test_version')
        upload_id = published.upload_id
        entry = Entry.objects(upload_id=upload_id).first()
        assert entry.nomad_version != 'test_version'

        async with temporal_worker():
            result = await asyncio.to_thread(
                lambda: invoke_cli(
                    cli,
                    ['admin', 'uploads', 'process', '--parallel', '2', upload_id],
                    catch_exceptions=False,
                )
            )

            assert result.exit_code == 0
            assert 'processing' in result.stdout
            entry.reload()
            assert entry.nomad_version == 'test_version'

    @pytest.mark.asyncio
    async def test_publish(
        self, non_empty_processed_with_temporal, monkeypatch, temporal_worker
    ):
        async with temporal_worker():
            monkeypatch.setattr('nomad.config.meta.version', 'test_version')
            upload_id = non_empty_processed_with_temporal.upload_id
            upload = Upload.objects(upload_id=upload_id).first()
            assert upload.publish_time is None
            assert upload.embargo_length == 0

            result = await asyncio.to_thread(
                lambda: invoke_cli(
                    cli,
                    ['admin', 'uploads', 'publish', '--embargo-length', '2', upload_id],
                    catch_exceptions=False,
                )
            )

            assert result.exit_code == 0
            assert 'publishing' in result.stdout
            upload.reload()
            assert upload.publish_time is not None
            assert upload.embargo_length == 2

    def test_unpublish(self, published):
        upload_id = published.upload_id
        upload = Upload.objects(upload_id=upload_id).first()
        result = invoke_cli(
            cli,
            ['admin', 'uploads', 'unpublish', upload_id],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        upload.reload()
        assert upload.publish_time is None
        assert upload.embargo_length == 0

    def test_re_pack(self, elastic_function, published, monkeypatch):
        upload_id = published.upload_id
        entry = Entry.objects(upload_id=upload_id).first()
        assert published.with_embargo
        published.embargo_length = 0
        published.save()

        result = invoke_cli(
            cli, ['admin', 'uploads', 're-pack', upload_id], catch_exceptions=False
        )

        assert result.exit_code == 0
        assert 're-pack' in result.stdout
        entry.reload()
        upload_files = files.PublicUploadFiles(upload_id)
        for path_info in upload_files.raw_listdir(recursive=True, files_only=True):
            with upload_files.raw_file(path_info.path) as f:
                f.read()
        for entry in Entry.objects(upload_id=upload_id):
            with upload_files.read_archive(entry.entry_id) as archive:
                assert entry.entry_id in archive

        published.reload()
        assert published.process_status == ProcessStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_chown(
        self, elastic_function, published: Upload, user1, user2, temporal_worker
    ):
        upload_id = published.upload_id
        assert published.main_author == user1.user_id
        with published.entries_metadata() as entries_metadata:
            for entry_metadata in entries_metadata:
                assert entry_metadata.main_author.user_id == user1.user_id

        async with temporal_worker():
            result = await asyncio.to_thread(
                lambda: invoke_cli(
                    cli,
                    ['admin', 'uploads', 'chown', user2.username, upload_id],
                    catch_exceptions=False,
                )
            )

            assert result.exit_code == 0
            assert 'changing' in result.stdout

            await published.await_workflows()

        assert published.main_author == user2.user_id
        with published.entries_metadata() as entries_metadata:
            for entry_metadata in entries_metadata:
                assert entry_metadata.main_author.user_id == user2.user_id

    @pytest.mark.parametrize(
        'with_entries,success,failure',
        [
            (True, False, False),
            (False, False, False),
            (True, True, False),
            (False, False, True),
        ],
    )
    def test_reset(self, user1, mongo_function, with_entries, success, failure):
        upload_id = 'test_reset_upload'
        data = ExampleData(main_author=user1)
        data.create_upload(upload_id=upload_id)
        data.create_entry(upload_id=upload_id)
        data.save(with_es=False, with_files=False)

        upload = Upload.objects(upload_id=upload_id).first()
        entry = Entry.objects(upload_id=upload_id).first()
        assert upload.process_status == ProcessStatus.SUCCESS
        assert entry.process_status == ProcessStatus.SUCCESS

        args = ['admin', 'uploads', 'reset']
        if with_entries:
            args.append('--with-entries')
        if success:
            args.append('--success')
        if failure:
            args.append('--failure')
        args.append(upload_id)
        result = invoke_cli(cli, args, catch_exceptions=False)

        assert result.exit_code == 0
        assert 'reset' in result.stdout
        upload = Upload.objects(upload_id=upload_id).first()
        entry = Entry.objects(upload_id=upload_id).first()

        expected_state = ProcessStatus.READY
        if success:
            expected_state = ProcessStatus.SUCCESS
        if failure:
            expected_state = ProcessStatus.FAILURE
        assert upload.process_status == expected_state
        if not with_entries:
            assert entry.process_status == ProcessStatus.SUCCESS
        else:
            assert entry.process_status == expected_state

    @pytest.mark.parametrize('all_entries', ['--check-all-entries', ''])
    @pytest.mark.parametrize('indexed', [True, False])
    @pytest.mark.parametrize('check_item', ['--entry-mismatch', '--missing-index'])
    def test_integrity_entry_index(
        self,
        user1,
        mongo_function,
        elastic_function,
        raw_files_function,
        indexed,
        check_item,
        all_entries,
    ):
        data = ExampleData(main_author=user1)
        try:
            data.create_upload(upload_id='test_upload')
            data.create_entry(upload_id='test_upload')
            data.save(with_es=indexed, with_files=False)

            result = invoke_cli(
                cli,
                f'admin uploads integrity {all_entries} {check_item} test_upload',
                catch_exceptions=True,
            )

            assert result.exit_code == 0
            assert ('test_upload' in result.output) != indexed
        finally:
            data.delete()

    @pytest.mark.parametrize('all_entries', ['--check-all-entries', ''])
    def test_integrity_archive(
        self,
        user1,
        mongo_function,
        elastic_function,
        raw_files_function,
        all_entries,
    ):
        data = ExampleData(main_author=user1)
        try:
            data.create_upload(upload_id='test_upload')
            data.create_entry(upload_id='test_upload')
            data.save(with_es=True, with_files=True)

            result = invoke_cli(
                cli,
                f'admin uploads integrity {all_entries} --old-archive-format test_upload',
                catch_exceptions=True,
            )

            assert result.exit_code == 0
            assert 'test_upload' not in result.output
        finally:
            data.delete()

    @pytest.mark.parametrize('all_entries', ['--check-all-entries', ''])
    @pytest.mark.parametrize('es_nomad_version', ['1', '2'])
    @pytest.mark.parametrize('archive_nomad_version', ['1', '2'])
    def test_integrity_nomad_version(
        self,
        monkeypatch,
        user1,
        mongo_function,
        elastic_function,
        raw_files_function,
        es_nomad_version,
        archive_nomad_version,
        all_entries,
    ):
        data = ExampleData(main_author=user1)
        try:
            data.create_upload(upload_id='test_upload')
            data.create_entry(upload_id='test_upload')
            data.save(
                with_es=True,
                with_files=True,
                es_nomad_version=es_nomad_version,
                archive_nomad_version=archive_nomad_version,
            )

            result = invoke_cli(
                cli,
                f'admin uploads integrity {all_entries} --nomad-version-mismatch test_upload',
                catch_exceptions=True,
            )

            assert result.exit_code == 0
            assert ('test_upload' in result.output) != (
                es_nomad_version == archive_nomad_version
            )
        finally:
            data.delete()

    @pytest.mark.parametrize('all_entries', ['--check-all-entries', ''])
    def test_integrity_suffix(
        self,
        monkeypatch,
        user1,
        mongo_function,
        elastic_function,
        raw_files_function,
        all_entries,
    ):
        data = ExampleData(main_author=user1)
        try:
            data.create_upload(upload_id='test_upload')
            data.create_entry(upload_id='test_upload')
            data.save(with_es=True, with_files=True)

            current_suffix = config.fs.archive_version_suffix
            if isinstance(current_suffix, list):
                new_suffix = ['whatever'] + current_suffix
            else:
                new_suffix = ['whatever', current_suffix]

            monkeypatch.setattr('nomad.config.fs.archive_version_suffix', new_suffix)

            result = invoke_cli(
                cli,
                f'admin uploads integrity {all_entries} --not-preferred-suffix test_upload',
                catch_exceptions=True,
            )

            assert result.exit_code == 0
            assert 'test_upload' in result.output
        finally:
            data.delete()

    @pytest.mark.parametrize('all_entries', ['--check-all-entries', ''])
    @pytest.mark.parametrize('delete_file', [True, False])
    @pytest.mark.parametrize(
        'check_item',
        ['--missing-storage', '--missing-raw-files', '--missing-archive-files'],
    )
    def test_integrity_files(
        self,
        user1,
        mongo_function,
        elastic_function,
        raw_files_function,
        check_item,
        delete_file,
        all_entries,
    ):
        upload_id = 'test_upload'
        mainfile = 'test_mainfile'
        data = ExampleData(main_author=user1)
        try:
            data.create_upload(upload_id=upload_id)
            data.create_entry(upload_id=upload_id, mainfile=mainfile, files=[mainfile])
            data.save(with_es=True, with_files=True)

            if delete_file:
                Upload.get(upload_id).upload_files.delete()
            result = invoke_cli(
                cli,
                f'admin uploads integrity {all_entries} {check_item} {upload_id}',
                catch_exceptions=True,
            )

            assert result.exit_code == 0
            assert ('test_upload' in result.output) == delete_file
        finally:
            data.delete()

    def test_integrity_both_storages(
        self, user1, mongo_function, elastic_function, raw_files_function
    ):
        upload_id = 'test_upload'
        data = ExampleData(main_author=user1)
        try:
            data.create_upload(upload_id=upload_id)
            data.create_entry(upload_id=upload_id)
            data.save(with_es=True, with_files=True)

            result = invoke_cli(
                cli,
                f'admin uploads integrity --both-storages {upload_id}',
                catch_exceptions=True,
            )

            assert result.exit_code == 0
            assert upload_id not in result.output
        finally:
            data.delete()


@pytest.mark.usefixtures('reset_config')
class TestClient:
    @pytest.mark.asyncio
    async def test_upload(
        self, non_empty_example_upload, user0, temporal_worker, client_with_api_v1
    ):
        def run():
            result = invoke_cli(
                cli,
                [
                    'client',
                    '-u',
                    user0.username,
                    '--token-via-api',
                    'upload',
                    '--upload-name',
                    'test_upload',
                    '--local-path',
                    non_empty_example_upload,
                ],
                catch_exceptions=False,
            )

            assert result.exit_code == 0, result.output
            assert '1/0/1' in result.output
            assert proc.Upload.objects(upload_name='test_upload').first() is not None

        async with temporal_worker():
            await asyncio.to_thread(run)

    def test_local(
        self, elastic_function, published_wo_user_metadata, client_with_api_v1
    ):
        result = invoke_cli(
            cli,
            [
                'client',
                'local',
                '--',
                published_wo_user_metadata.successful_entries[0].entry_id,
            ],
            catch_exceptions=True,
        )

        assert result.exit_code == 0, result.output

    @pytest.mark.skip('Disabled. Tested code is temporaely commented.')
    def test_statistics(self):
        result = invoke_cli(cli, ['client', 'statistics-table'], catch_exceptions=True)

        assert result.exit_code == 0, result.output
        assert 'Calculations, e.g. total energies' in result.output
        assert 'Geometries' in result.output
        assert 'Bulk crystals' in result.output
        assert '2D / Surfaces' in result.output
        assert 'Atoms / Molecules' in result.output
        assert 'DOS' in result.output
        assert 'Band structures' in result.output


@pytest.mark.usefixtures('reset_config')
class TestDev:
    def test_parser_metadata(self):
        result = invoke_cli(cli, ['dev', 'parser-metadata'], catch_exceptions=True)

        assert result.exit_code == 0, result.output
        assert 'yambo' in result.output
        assert 'lammps' in result.output
        assert 'elastic' in result.output
