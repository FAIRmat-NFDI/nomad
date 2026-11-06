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
import { range } from 'lodash'
import { TestAdaptor } from './conftest.spec'
import {screen, renderNoAPI, render} from '../conftest.spec'
import { expectPagination } from '../visualization/conftest.spec'
import { archiveAdaptorFactory, PropertyValuesList, Section } from './ArchiveBrowser'
import { laneContext } from './Browser'
import {waitFor, within} from "@testing-library/dom"
import {Metainfo} from './metainfo'
import {createEntryUrl, systemMetainfoUrl} from '../../utils'
import {apiBase} from '../../config'

test.each([
  [15, 10, 5],
  [12, 10, 5]
])('test subsection with no pagination, items: %s, top: %s, bottom:%s', async (nItems, nTop, nBottom) => {
  const indices = range(nItems)
  const label = "subsection"
  const values = indices.map(i => (null))

  renderNoAPI(
    <laneContext.Provider value={{next: {}}}>
      <PropertyValuesList itemKey={label} values={values} nTop={nTop} nBottom={nBottom} open={true} />
    </laneContext.Provider>
  )

  // Expect to find all items
  for (const i of indices) {
    screen.getByText(`${i}`)
  }
  // Pagination component should not be visible
  await expectPagination(false, false, false)
})

test.each([
  [30, 10, 5],
  [16, 10, 5],
  [30, 10, 0]
])('test subsection with pagination, items: %s, top: %s, bottom:%s', async (nItems, nTop, nBottom) => {
  const indices = range(nItems)
  const label = "subsection"
  const values = indices.map(i => (null))

  renderNoAPI(
    <laneContext.Provider value={{next: {}}}>
      <PropertyValuesList itemKey={label} values={values} nTop={nTop} nBottom={nBottom} open={true} />
    </laneContext.Provider>
  )

  // Expect to find top and bottom items
  for (const i of range(nTop)) {
    screen.getByText(`${i}`)
  }
  for (const i of range(nBottom)) {
    screen.getByText(`${nItems - i - 1}`)
  }
  // Both pagination components should be visible
  const downPagination = screen.getByTestId('propertyvalueslist-pagination-down')
  await expectPagination(true, false, false, downPagination)
  if (nBottom > 0) {
    const upPagination = screen.getByTestId('propertyvalueslist-pagination-up')
    await expectPagination(true, false, false, upPagination)
  } else {
    await waitFor(() => expect(screen.queryByTestId('propertyvalueslist-pagination-up')).not.toBeInTheDocument())
  }
})

async function createMetainfo(data, parent, url = systemMetainfoUrl) {
  data._url = url
  data._metainfo = new Metainfo(parent, data, null, {}, {})
  return await data._metainfo._result
}

const mockPackage = (properties, sectionDisplay = undefined, quantityDisplay = undefined) => {
  const stringEditQuantity = (name, display = undefined) => (
    {
      name: name,
      default: name,
      type: { type_kind: 'python', type_data: 'str' },
      m_annotations: {
        eln: [
          {
            component: "StringEditQuantity"
          }
        ],
        display: [display]
      },
      m_parent_sub_section: 'quantities'
    }
  )

  return {
    packages: [
      {
        name: 'testPackage',
        section_definitions: [
          {
            name: 'TestSection',
            m_annotations: {
              eln: properties && [
                {
                  properties: properties
                }
              ],
              display: [sectionDisplay]
            },
            quantities: [
              stringEditQuantity('value1', quantityDisplay?.[0]),
              stringEditQuantity('value2', quantityDisplay?.[1]),
              stringEditQuantity('value3', quantityDisplay?.[2])
            ]
          }
        ]
      }
    ]
  }
}

test.each([
  [
    'default',
    undefined,
    ['value1', 'value2', 'value3'],
    [],
    [true, true, true]
  ],
  [
    'visible included',
    {
      visible: {
        include: ['value3', 'value2']
      }
    },
    ['value2', 'value3'],
    ['value1'],
    [true, true]
  ],
  [
    'visible excluded',
    {
      visible: {
        exclude: ['value2']
      }
    },
    ['value1', 'value3'],
    ['value2'],
    [true, true]
  ],
  [
    'visible included and excluded',
    {
      visible: {
        include: ['value1', 'value3'],
        exclude: ['value1']
      }
    },
    ['value3'],
    ['value1', 'value2'],
    [true]
  ],
  [
    'order',
    {
      order: ['value3', 'value2', 'value1']
    },
    ['value3', 'value2', 'value1'],
    [],
    [true, true, true]
  ],
  [
    'ordered and visible',
    {
      visible: {
        include: ['value1', 'value3']
      },
      order: ['value3', 'value1']
    },
    ['value3', 'value1'],
    ['value2'],
    [true, true]
  ],
  [
    'editable excluded',
    {
      editable: {
        exclude: ['value2']
      }
    },
    ['value1', 'value2', 'value3'],
    [],
    [true, false, true]
  ],
  [
    'editable included',
    {
      editable: {
        include: ['value2']
      }
    },
    ['value1', 'value2', 'value3'],
    [],
    [false, true, false]
  ],
  [
    'editable included and excluded',
    {
      editable: {
        include: ['value1', 'value3'],
        exclude: ['value1']
      }
    },
    ['value1', 'value2', 'value3'],
    [],
    [false, false, true]
  ],
  [
    'ordered and editable',
    {
      editable: {
        exclude: ['value1', 'value3']
      },
      order: ['value3', 'value1']
    },
    ['value3', 'value1', 'value2'],
    [],
    [false, false, true]
  ],
  [
    'ordered and editable and visible',
    {
      editable: {
        exclude: ['value1', 'value3']
      },
      visible: {
        exclude: ['value2']
      },
      order: ['value3', 'value1']
    },
    ['value3', 'value1'],
    ['value2'],
    [false, false]
  ]
])('test eln properties: %s', async (name, properties, orderExpected, hiddenExpected, editableExpected) => {
  const metainfo = await createMetainfo(mockPackage(properties))
  const defsByName = await metainfo.getDefsByName()
  const def = defsByName.TestSection[0]
  const adaptor = new TestAdaptor('', 'Data')

  render(
    <laneContext.Provider value={{next: {}, adaptor: adaptor}}>
      <Section
        section={{}}
        def={def}
        sectionIsInEln={true}
        sectionIsEditable={true}
      />
    </laneContext.Provider>
  )

  const fields = screen.getAllByTestId('visible-quantity')
  expect(fields).toHaveLength(orderExpected.length)

  // test the order and editability of the visible values
  for (const [index, value] of orderExpected.entries()) {
    if (editableExpected[index]) {
      expect(within(fields[index]).getByText(value)).toBeInTheDocument()
      expect(within(fields[index]).getByTestId('editable-quantity-editor')).toBeInTheDocument()
    } else {
      expect(within(fields[index]).getByTestId(`quantity-${value}`)).toBeInTheDocument()
      expect(within(fields[index]).queryByTestId('editable-quantity-editor')).not.toBeInTheDocument()
    }
  }

  // make sure hidden values are not in the screen
  for (const value of hiddenExpected.values()) {
    expect(screen.queryByText(value)).not.toBeInTheDocument()
  }
})

test.each([
  [
    'default',
    undefined,
    undefined,
    ['value1', 'value2', 'value3'],
    [],
    [true, true, true]
  ],
  [
    'order',
    {
      order: ['value3', 'value2', 'value1']
    },
    undefined,
    ['value3', 'value2', 'value1'],
    [],
    [true, true, true]
  ],
  [
    'visibility',
    undefined,
    [
      {visible: true},
      {visible: false},
      undefined
    ],
    ['value1', 'value3'],
    ['value2'],
    [true, true]
  ],
  [
    'order and visibility',
    {
      order: ['value3', 'value1']
    },
    [
      {visible: true},
      {visible: false},
      undefined
    ],
    ['value3', 'value1'],
    ['value2'],
    [true, true]
  ],
  [
    'editability',
    undefined,
    [
      {editable: true},
      {editable: false},
      undefined
    ],
    ['value1', 'value2', 'value3'],
    [],
    [true, false, true]
  ],
  [
    'order and editiblity',
    {
      order: ['value3', 'value1']
    },
    [
      {editable: true},
      {editable: false},
      undefined
    ],
    ['value3', 'value1', 'value2'],
    [],
    [true, true, false]
  ],
  [
    'order and editiblity and visibility',
    {
      order: ['value3', 'value1']
    },
    [
      {editable: true, visible: false},
      {editable: false, visible: true},
      {visible: true}
    ],
    ['value3', 'value2'],
    ['value1'],
    [true, false]
  ],
  [
    'section visible included',
    {
      visible: {
        include: ['value3', 'value2']
      }
    },
    undefined,
    ['value2', 'value3'],
    ['value1'],
    [true, true]
  ],
  [
    'section visible excluded',
    {
      visible: {
        exclude: ['value2']
      }
    },
    undefined,
    ['value1', 'value3'],
    ['value2'],
    [true, true]
  ],
  [
    'section visible included and excluded',
    {
      visible: {
        include: ['value1', 'value3'],
        exclude: ['value1']
      }
    },
    undefined,
    ['value3'],
    ['value1', 'value2'],
    [true]
  ],
  [
    'section editable excluded',
    {
      editable: {
        exclude: ['value2']
      }
    },
    undefined,
    ['value1', 'value2', 'value3'],
    [],
    [true, false, true]
  ],
  [
    'section editable included',
    {
      editable: {
        include: ['value2']
      }
    },
    undefined,
    ['value1', 'value2', 'value3'],
    [],
    [false, true, false]
  ],
  [
    'section editable included and excluded',
    {
      editable: {
        include: ['value1', 'value3'],
        exclude: ['value1']
      }
    },
    undefined,
    ['value1', 'value2', 'value3'],
    [],
    [false, false, true]
  ],
  [
    'section ordered and editable',
    {
      editable: {
        exclude: ['value1', 'value3']
      },
      order: ['value3', 'value1']
    },
    undefined,
    ['value3', 'value1', 'value2'],
    [],
    [false, false, true]
  ],
  [
    'section ordered and editable and visible',
    {
      editable: {
        exclude: ['value1', 'value3']
      },
      visible: {
        exclude: ['value2']
      },
      order: ['value3', 'value1']
    },
    undefined,
    ['value3', 'value1'],
    ['value2'],
    [false, false]
  ],
  [
    'section and quantity with visible',
    {
      visible: {
        exclude: ['value2']
      }
    },
    [
      {visible: false},
      {visible: true},
      undefined
    ],
    ['value2', 'value3'],
    ['value1'],
    [true, true]
  ],
  [
    'section and quantity with editable',
    {
      editable: {
        exclude: ['value2']
      }
    },
    [
      {editable: false},
      {editable: true},
      undefined
    ],
    ['value1', 'value2', 'value3'],
    [],
    [false, true, true]
  ],
  [
    'section and quantity with order, editable and visible',
    {
      editable: {
        exclude: ['value1', 'value3']
      },
      visible: {
        exclude: ['value2']
      },
      order: ['value3', 'value1']
    },
    [
      {editable: true, visible: false},
      {visible: true},
      {editable: false}
    ],
    ['value3', 'value2'],
    ['value1'],
    [false, true]
  ]
])('test display annotation: %s', async (name, sectionDisplay, quantityDisplay, orderExpected, hiddenExpected, editableExpected) => {
  const metainfo = await createMetainfo(mockPackage(undefined, sectionDisplay, quantityDisplay))
  const defsByName = await metainfo.getDefsByName()
  const def = defsByName.TestSection[0]
  const adaptor = new TestAdaptor('', 'Data')

  render(
    <laneContext.Provider value={{next: {}, adaptor: adaptor}}>
      <Section
        section={{}}
        def={def}
        sectionIsInEln={true}
        sectionIsEditable={true}
      />
    </laneContext.Provider>
  )

  const fields = screen.getAllByTestId('visible-quantity')
  expect(fields).toHaveLength(orderExpected.length)

  // test the order and editability of the visible values
  for (const [index, value] of orderExpected.entries()) {
    if (editableExpected[index]) {
      expect(within(fields[index]).getByText(value)).toBeInTheDocument()
      expect(within(fields[index]).getByTestId('editable-quantity-editor')).toBeInTheDocument()
    } else {
      expect(within(fields[index]).getByTestId(`quantity-${value}`)).toBeInTheDocument()
      expect(within(fields[index]).queryByTestId('editable-quantity-editor')).not.toBeInTheDocument()
    }
  }

  // make sure hidden values are not in the screen
  for (const value of hiddenExpected.values()) {
    expect(screen.queryByText(value)).not.toBeInTheDocument()
  }
})

test('list quantity with eln annotation but no component can be navigated when editable', async () => {
  const metainfo = await createMetainfo({
    packages: [
      {
        name: 'testPackage',
        section_definitions: [
          {
            name: 'TestSection',
            quantities: [
              {
                name: 'aggregated_elements',
                type: { type_kind: 'python', type_data: 'str' },
                shape: ['*'],
                m_annotations: {eln: [{label: 'aggregated elements'}]},
                m_parent_sub_section: 'quantities'
              }
            ]
          }
        ]
      }
    ]
  })
  const defsByName = await metainfo.getDefsByName()
  const def = defsByName.TestSection[0]
  const adaptor = archiveAdaptorFactory(createEntryUrl(apiBase, 'upload_id', 'entry_id'), {aggregated_elements: ['A', 'B', 'C']}, def)
  adaptor.entryIsEditable = true

  // The whole list is shown when navigating without an index
  expect((await adaptor.itemAdaptor('aggregated_elements')).obj).toEqual(['A', 'B', 'C'])
  // A single element is shown when navigating with an index
  expect((await adaptor.itemAdaptor('aggregated_elements:1')).obj).toEqual('B')
})
