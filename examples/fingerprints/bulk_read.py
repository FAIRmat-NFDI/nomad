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
"""
In this example, we go through many uploads in parallel to extract information from
certain calculations.

The motivation behind this is that the selective access of sections in an archvie might
be slow. If something is read from almost all calculations, it might be faster to
sequentially read all of the upload's archive file.

This is not an API example, but directly accesses archive files. Specifically, we
try to read all fingerprints for upload/calc/material combinations read from an
input file. The fingerprint data gets writting to an output file.
"""

import json
import traceback
from multiprocessing import Event, Pool, Queue
from queue import Empty
from typing import Any

from nomad import files
from nomad.archive import to_json


def read_archive(entries):
    try:
        upload_id = entries[0]['upload_id']
        upload_files = files.UploadFiles.get(upload_id)
        assert upload_files is not None
        for entry in entries:
            entry_id = entry['entry_id']
            material_id = entry['material_id']
            with upload_files.read_archive(entry_id) as archive:
                entry_archive = to_json(archive[entry_id])
                for run in entry_archive.get('section_run', []):
                    for calc in run.get('section_single_configuration_calculation', []):
                        for dos in calc.get('section_dos', []):
                            fingerprint = dos.get('section_dos_fingerprint')
                            if fingerprint:
                                yield {
                                    'upload_id': upload_id,
                                    'entry_id': entry_id,
                                    'material_id': material_id,
                                    'fingerprint': fingerprint,
                                }
    except Exception:
        traceback.print_exc()


nworker = 24
entry_queue: Any = Queue(maxsize=100)
result_queue: Any = Queue(maxsize=100)
producer_end = Event()
worker_sentinel = 'end'


def worker():
    entries = []
    while not (producer_end.is_set() and entry_queue.empty()):
        try:
            entries = entry_queue.get(block=True, timeout=0.1)
        except Empty:
            continue

        for result in read_archive(entries):
            result_queue.put(result)

    result_queue.put(worker_sentinel)
    print('end worker')


def writer():
    ended_worker = 0
    count = 0
    f = open('local/fingerprints.json', 'w')
    f.write('[')
    while not (ended_worker == nworker and result_queue.empty()):
        try:
            result = result_queue.get(block=True, timeout=0.1)
        except Empty:
            continue

        if result == worker_sentinel:
            ended_worker += 1
            continue

        if count > 0:
            f.write(',\n')
        json.dump(result, f, indent=2)
        count += 1
        if count % 1000 == 0:
            print(count)

    f.write(']')
    f.close()
    print('end writer')


def producer():
    with open('local/materials.json') as f:
        data = json.load(f)

    upload_id = None
    entries = []
    for entry in data:
        if upload_id is not None and upload_id != entry['upload_id']:
            entry_queue.put(entries, block=True)
            entries = []

        upload_id = entry['upload_id']
        entries.append(entry)

    entry_queue.put(entries, block=True)

    producer_end.set()
    print('end producer')


with Pool(processes=nworker + 2) as pool:
    for _ in range(nworker):
        pool.apply_async(worker)
    pool.apply_async(producer)
    pool.apply_async(writer)

    pool.close()
    pool.join()
