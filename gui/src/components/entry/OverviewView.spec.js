/*
 * Copyright The NOMAD Authors.
 *
 * This file is part of NOMAD. See https://nomad-lab.eu for further info.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import React from 'react'
import 'regenerator-runtime/runtime'
import { fireEvent, cleanup } from '@testing-library/react'
import { waitFor, within } from '@testing-library/dom'
import { render, screen, expectQuantity, readArchive, startAPI, closeAPI, waitForGUI } from '../conftest.spec'
import { expectPlotButtons } from '../visualization/conftest.spec'
import {
  expectComposition,
  expectSymmetry,
  expectLatticeParameters
} from './conftest.spec'
import OverviewView from './OverviewView'
import { ui } from '../../config'
import { EntryContext } from './EntryContext'
import userEvent from '@testing-library/user-event'
import { act } from 'react-dom/test-utils'

test.each([
  ['material', 'material', 'Material'],
  ['electronic', 'dos_electronic', 'Electronic properties'],
  ['mechanical', 'bulk_modulus', 'Mechanical properties'],
  ['thermodynamic', 'trajectory', 'Thermodynamic properties'],
  ['vibrational', 'dos_phonon', 'Vibrational properties'],
  ['structural', 'rdf', 'Structural properties']
])('correctly renders %s card when card is enabled/disabled in config', async (card, state, label) => {
  await startAPI(`tests.states.entry.${state}`, `tests/data/entry/${card}_card`)
  for (const enabled of [true, false]) {
    const cards = enabled ? {options: {card: ui.entry.cards.options[card]}} : {options: {}}
    render(
      <EntryContext entryId={'dft_bulk'} cards={cards}>
        <OverviewView/>
      </EntryContext>
    )

    // Wait until initial render is done.
    const firstLabel = 'Metadata'
    expect(await screen.findByText(firstLabel))

    // Check that the correct sections are shown
    enabled
      ? expect(screen.getByText(label))
      : expect(screen.queryByText(label)).toBeNull()

    // Manual cleanup since we are running render several times.
    cleanup()
  }
  closeAPI()
})

test('correctly renders metadata and all properties', async () => {
  await startAPI('tests.states.entry.dft', 'tests/data/entry/dft')
  await act(async () => render(
    <EntryContext entryId={'dft_bulk'}>
      <OverviewView />
    </EntryContext>
  ))

  // Wait to load the entry metadata, i.e. wait for some of the text to appear
  await screen.findByText('VASP')

  // We read the JSON archive corresponding to the tested API entry. Using this
  // data makes writing assertions much easier.
  const {index} = (await readArchive('../../../tests/states/archives/dft.json'))

  // Check if all method quantities are shown (on the left)
  expectQuantity('results.method.simulation.program_name', 'VASP')
  expectQuantity('results.method.simulation.program_version', '1')
  expectQuantity('results.method.simulation.dft.xc_functional_type', 'GGA')
  expectQuantity('results.method.simulation.dft.xc_functional_names', 'GGA_C_PBE, GGA_X_PBE')
  expectQuantity('results.method.simulation.dft.basis_set_type', 'plane waves')
  expectQuantity('results.method.simulation.dft.van_der_Waals_method', 'G06')
  expectQuantity('results.method.simulation.dft.relativity_method', 'scalar_relativistic_atomic_ZORA')

  // Check if all metadata is shown (on the left)
  expectQuantity('results.method.method_name', index)
  expectQuantity('mainfile', index)
  expectQuantity('entry_id', index)
  expectQuantity('upload_id', index)
  expectQuantity('results.material.material_id', index)
  expectQuantity(undefined, `${index.nomad_version}/${index.nomad_commit}`, 'Processing version', 'Version used in the last processing')
  // test the quantities with presets label
  expectQuantity('comment', index)
  expectQuantity('references', index.references[0])
  expectQuantity('authors', 'Markus Scheidgen')
  // TODO: add the following to the state for testing.
  // expectQuantity('datasets', index.datasets[0].dataset_name)
  // expectQuantity('upload_create_time', formatTimestamp(index.upload_create_time))
  // expectQuantity('last_processing_time', formatTimestamp(index.last_processing_time))

  // Check if all material data is shown (on the right, in the materials card)
  expectComposition(index)
  expectSymmetry(index)
  expectLatticeParameters(index)
  // expectStructure(index) // TODO: The click introduced here breaks the subsequent tests

  // Check if all the property cards are shown
  expect(screen.getByText('Electronic properties')).toBeInTheDocument()
  expect(screen.getByText('Band structure')).toBeInTheDocument()
  expect(screen.getByText('Density of states')).toBeInTheDocument()
  expect(screen.getByText('Brillouin zone')).toBeInTheDocument()

  expect(screen.getByText('Vibrational properties')).toBeInTheDocument()
  expect(screen.getByText('Phonon dispersion')).toBeInTheDocument()
  expect(screen.getByText('Phonon density of states')).toBeInTheDocument()
  expect(screen.getByText('Heat capacity')).toBeInTheDocument()
  expect(screen.getByText('Helmholtz free energy')).toBeInTheDocument()
  expect(screen.getByText('Mechanical properties')).toBeInTheDocument()
  expect(screen.getByText('Energy-volume curve')).toBeInTheDocument()
  expect(screen.getByText('Bulk modulus')).toBeInTheDocument()
  expect(screen.getByText('Shear modulus')).toBeInTheDocument()

  // Check if all placeholders disappear
  const dosPhononPlaceholder = screen.queryByTestId('dos-phonon-placeholder')
  const bsPhononPlaceholder = screen.queryByTestId('bs-phonon-placeholder')
  const heatCapacityPlaceholder = screen.queryByTestId('heat-capacity-placeholder')
  const energyFreePlaceholder = screen.queryByTestId('energy-free-placeholder')
  const dosElectronicPlaceholder = screen.queryByTestId('dos-electronic-placeholder')
  const bsElectronicPlaceholder = screen.queryByTestId('bs-electronic-placeholder')
  const energyVolumeCurvePlaceholder = screen.queryByTestId('energy-volume-curve-placeholder')
  await waitFor(() => { expect(dosElectronicPlaceholder).not.toBeInTheDocument() })
  await waitFor(() => { expect(bsElectronicPlaceholder).not.toBeInTheDocument() })
  await waitFor(() => { expect(dosPhononPlaceholder).not.toBeInTheDocument() })
  await waitFor(() => { expect(bsPhononPlaceholder).not.toBeInTheDocument() })
  await waitFor(() => { expect(heatCapacityPlaceholder).not.toBeInTheDocument() })
  await waitFor(() => { expect(energyFreePlaceholder).not.toBeInTheDocument() })
  await waitFor(() => { expect(energyVolumeCurvePlaceholder).not.toBeInTheDocument() })

  // The test DOM does not support canvas or WebGL, and trying to add mocks for
  // them does not seem to work ATM. Thus we expect a message saying that the
  // 3D viewers are disabled.
  const msgs = screen.getAllByText('Could not display the visualization as your browser does not support WebGL content.')
  expect(msgs).toHaveLength(2)

  // Check that plot buttons are displayed
  const dosElectronic = screen.getByTestId('dos-electronic')
  expectPlotButtons(dosElectronic)
  const bsElectronic = screen.getByTestId('bs-electronic')
  expectPlotButtons(bsElectronic)
  const bsPhonon = screen.getByTestId('bs-phonon')
  expectPlotButtons(bsPhonon)
  const dosPhonon = screen.getByTestId('dos-phonon')
  expectPlotButtons(dosPhonon)
  const heatCapacity = screen.getByTestId('heat-capacity')
  expectPlotButtons(heatCapacity)
  const energyFree = screen.getByTestId('energy-free')
  expectPlotButtons(energyFree)
  const energyVolumeCurve = screen.getByTestId('energy-volume-curve')
  expectPlotButtons(energyVolumeCurve)

  // Check that tables are shown
  const bulkModulus = screen.getByTestId('bulk-modulus')
  expect(within(bulkModulus).getByText('Type')).toBeInTheDocument()
  expect(within(bulkModulus).getByText('Value (GPa)')).toBeInTheDocument()
  expect(within(bulkModulus).getByText('murnaghan')).toBeInTheDocument()
  const shearModulus = screen.getByTestId('shear-modulus')
  expect(within(shearModulus).getByText('Type')).toBeInTheDocument()
  expect(within(shearModulus).getByText('Value (GPa)')).toBeInTheDocument()
  expect(within(shearModulus).getByText('voigt_reuss_hill_average')).toBeInTheDocument()

  closeAPI()
})

function expectQuantityToBe(name, label, value, root = screen) {
  const element = root.queryByTestId(`quantity-${name}`)
  expect(within(element).getByText(label)).toBeInTheDocument()
  if (value === undefined) return
  const values = Array.isArray(value) ? value : [value]
  values.forEach(expectedValue => expect(within(element).queryAllByText(expectedValue).length).not.toBe(0))
}

function expectQuantityNotToBe(name, root = screen) {
  expect(root.queryByTestId(`quantity-${name}`)).not.toBeInTheDocument()
}

test('eln overview as a reviewer', async () => {
  await startAPI('tests.states.entry.eln', 'tests/data/entry/eln-reviewer', 'ttester', 'password')
  await act(async () => render(
    <EntryContext entryId="bC7byHvWJp62Sn9uiuJUB38MT5j-">
      <OverviewView />
    </EntryContext>
  ))

  // Wait until the initial load is done by checking one of the card titles
  await screen.findByText('HotplateAnnealing')

  expect(screen.queryByTitle("Replace this entry's mainfile")).not.toBeInTheDocument()
  expect(screen.queryByTitle('Save entry')).not.toBeInTheDocument()
  expect(screen.queryByTitle('Delete entry')).not.toBeInTheDocument()

  const sectionCards = screen.queryAllByTestId('property-card')
  expect(sectionCards.length).toBe(5)

  const cardSample = sectionCards[0]
  const cardPvdEvaporation = sectionCards[1]
  const cardHotplateAnnealing = sectionCards[2]

  expect(within(cardSample).getByText('Sample')).toBeVisible()
  // expectQuantityToBe('chemical_formula', 'chemical formula', undefined, within(cardSample))
  // test the quantities with deprecated eln annotation label
  expectQuantityToBe('name', 'Name', 'ELN example sample', within(cardSample))
  expectQuantityToBe('lab_id', 'ID', '001', within(cardSample))
  expectQuantityToBe('description', 'Description', undefined, within(cardSample))
  expectQuantityToBe('tags', 'Tags', 'project', within(cardSample))
  expectQuantityToBe('substrate_type', 'Substrate type', 'SLG', within(cardSample))
  expectQuantityNotToBe('substrate_thickness', within(cardSample))
  expectQuantityNotToBe('sample_is_from_collaboration', within(cardSample))

  expect(within(cardPvdEvaporation).getByText('PvdEvaporation')).toBeVisible()
  expectQuantityToBe('data_file', 'Data file', 'PVDProcess.csv', within(cardPvdEvaporation))

  // Test if the open plots are there
  const plotlyFigures = within(cardPvdEvaporation).getAllByTestId('plotly-figure')
  expect(plotlyFigures.length).toBe(2)

  expect(within(plotlyFigures[0]).getByText(/Chamber Pressure \(GPa\)/)).toBeVisible()
  expect(within(plotlyFigures[0]).getByText(/Substrate Temperature \(K\)/)).toBeVisible()
  expect(within(plotlyFigures[0]).getByText(/Time \(fs\)/)).toBeVisible()

  expect(within(plotlyFigures[1]).queryByText(/Chamber Pressure \(GPa\)/)).not.toBeInTheDocument()
  expect(within(plotlyFigures[1]).getByText(/Substrate Temperature \(K\)/)).toBeVisible()
  expect(within(plotlyFigures[1]).getByText(/Time \(fs\)/)).toBeVisible()

  expect(within(cardHotplateAnnealing).getByText('HotplateAnnealing')).toBeVisible()
  expectQuantityNotToBe('instrument', within(cardHotplateAnnealing))
  expectQuantityNotToBe('method', within(cardHotplateAnnealing))
  expectQuantityToBe('set_temperature', 'Set temperature', '373.15', within(cardHotplateAnnealing))
  expectQuantityToBe('duration', 'Duration', '60', within(cardHotplateAnnealing))

  // Wait for the last API calls (e.g. reference card) to finish being recorded.
  await waitForGUI(5000)

  closeAPI()
})

function expectNumberEditQuantity(numberField, unitField, value, unit) {
  const numberFieldValueInput = within(numberField).getByRole('textbox')
  const numberFieldUnitInput = within(unitField).getByRole('textbox', { hidden: true })
  expect(numberFieldValueInput.value).toEqual(value)
  expect(numberFieldUnitInput.value).toEqual(unit)
}

test.each([
  [
    'an author',
    'tests.states.entry.eln',
    'tests/data/entry/eln-author',
    'bC7byHvWJp62Sn9uiuJUB38MT5j-',
    'test',
    'password'
  ],
  [
    'a coauthor',
    'tests.states.entry.eln',
    'tests/data/entry/eln-coauthor',
    'bC7byHvWJp62Sn9uiuJUB38MT5j-',
    'scooper',
    'password'
  ]
])('eln overview as %s', async (name, state, snapshot, entryId, username, password) => {
  await startAPI(state, snapshot, username, password)
  await act(async () => render(
    <EntryContext entryId={entryId}>
      <OverviewView />
    </EntryContext>
  ))

  // Wait until the initial load is done by checking one of the card titles
  await screen.findByText('HotplateAnnealing')

  const saveButton = screen.queryByTitle('Save entry').closest('button')
  expect(saveButton).toBeInTheDocument()
  expect(saveButton).toBeDisabled()

  const reUploadButton = screen.queryByTitle("Replace this entry's mainfile").closest('button')
  expect(reUploadButton).toBeInTheDocument()
  expect(reUploadButton).toBeEnabled()

  const deleteButton = screen.queryByTitle('Delete entry').closest('button')
  expect(deleteButton).toBeInTheDocument()
  expect(deleteButton).toBeEnabled()

  const sectionCards = screen.queryAllByTestId('property-card')
  expect(sectionCards.length).toBe(5)

  const cardSample = sectionCards[0]
  const cardPvdEvaporation = sectionCards[1]
  const cardHotplateAnnealing = sectionCards[2]

  expect(within(cardSample).getByText('Sample')).toBeVisible()
  let numberFieldValue = within(cardSample).queryAllByTestId('number-edit-quantity-value')
  let numberFieldUnit = within(cardSample).queryAllByTestId('number-edit-quantity-unit')
  expectNumberEditQuantity(numberFieldValue[0], numberFieldUnit[0], '', 'Å')

  // Test if the open plots are there
  const plotlyFigures = within(cardPvdEvaporation).getAllByTestId('plotly-figure')
  expect(plotlyFigures.length).toBe(2)

  expect(within(plotlyFigures[0]).getByText(/Chamber Pressure \(GPa\)/)).toBeVisible()
  expect(within(plotlyFigures[0]).getByText(/Substrate Temperature \(K\)/)).toBeVisible()
  expect(within(plotlyFigures[0]).getByText(/Time \(fs\)/)).toBeVisible()

  expect(within(plotlyFigures[1]).queryByText(/Chamber Pressure \(GPa\)/)).not.toBeInTheDocument()
  expect(within(plotlyFigures[1]).getByText(/Substrate Temperature \(K\)/)).toBeVisible()
  expect(within(plotlyFigures[1]).getByText(/Time \(fs\)/)).toBeVisible()

  numberFieldValue = within(cardHotplateAnnealing).queryAllByTestId('number-edit-quantity-value')
  numberFieldUnit = within(cardHotplateAnnealing).queryAllByTestId('number-edit-quantity-unit')
  expectNumberEditQuantity(numberFieldValue[0], numberFieldUnit[0], '373.15', 'K')

  // Wait for the last API calls (e.g. reference card) to finish being recorded.
  await waitForGUI(5000)

  closeAPI()
})

test.each([
  [
    'an author',
    'tests.states.entry.references',
    'tests/data/entry/eln-concurrent',
    'MOP2x0BEo4BrUikcqz591jmqI7sW',
    'test',
    'password'
  ]
])('eln concurrent editing', async (name, state, snapshot, entryId, username, password) => {
  await startAPI(state, snapshot, username, password)
  const screen1 = render(<EntryContext entryId={entryId}><OverviewView /></EntryContext>)

  // Wait until the initial load is done by checking one of the card titles
  await screen1.findByText('Sample')
  await screen1.findByTitle('Save entry')
  await screen1.findByTitle('Delete entry')

  const saveButton1 = screen1.getByTitle('Save entry').closest('button')
  expect(saveButton1).toBeInTheDocument()
  expect(saveButton1).toBeDisabled()

  const deleteButton1 = screen1.getByTitle('Delete entry').closest('button')
  expect(deleteButton1).toBeEnabled()
  await userEvent.click(deleteButton1)
  const deleteMainfileButton = await screen1.getByTestId('delete-dialog-delete-button').closest('button')

  await waitForGUI(1000)

  const screen2 = render(<EntryContext entryId={entryId}><OverviewView /></EntryContext>)

  // Wait until the initial load is done by checking one of the card titles
  await screen2.findByTitle('Save entry')

  // Weird jest problem: sometimes we find multiple hits for 'Save entry'.
  // Workaround: take the first one in the list.
  const saveButton2 = screen2.queryAllByTitle('Save entry')[0].closest('button')
  expect(saveButton2).toBeInTheDocument()
  expect(saveButton2).toBeDisabled()

  const sectionCards2 = screen2.queryAllByTestId('property-card')
  const cardSample2 = sectionCards2[0]
  const inputTextField2 = within(cardSample2).queryAllByRole('textbox', { hidden: true })
  await fireEvent.change(inputTextField2[0], { target: { value: 'new text 2' } })

  await userEvent.click(deleteMainfileButton)

  expect(saveButton2).toBeEnabled()
  await userEvent.click(saveButton2)
  await screen2.findByText('The changes cannot be saved. The content has been modified by someone else.')

  // Wait for the last API calls (e.g. reference card) to finish being recorded.
  await waitForGUI(1000)

  closeAPI()
})

test.each([
  [
    'an author',
    'tests.states.entry.eln',
    'tests/data/entry/eln-author-edit',
    'bC7byHvWJp62Sn9uiuJUB38MT5j-',
    'test',
    'password'
  ]
])('saving archive changes made in the overview page as %s', async (name, state, snapshot, entryId, username, password) => {
  await startAPI(state, snapshot, username, password)

  // Let's first render the initial state and see that the value is correct
  const {rerender} = render(<EntryContext entryId={entryId}><OverviewView /></EntryContext>)
  const input = await screen.findByDisplayValue('ELN example sample')

  // Clear old value and insert new one. For some reason clearing the input with
  // userEvent.clear or userEvent.type('{selectall}{backspace}') does not work
  // here.
  const newValue = 'new value'
  await fireEvent.change(input, {target: {value: newValue}})

  // Save entry, wait for the API processing
  const saveButton = screen.getByTitle('Save entry').closest('button')
  expect(saveButton).toBeEnabled()
  await userEvent.click(saveButton)
  await waitForGUI(2000)

  // Clear screen, re-render and see if value has changed
  rerender(<EntryContext entryId={entryId}><OverviewView /></EntryContext>)
  await screen.findByDisplayValue(newValue)

  // Wait for the last API calls (e.g. reference card) to finish being recorded.
  await waitForGUI(2000)

  closeAPI()
})
