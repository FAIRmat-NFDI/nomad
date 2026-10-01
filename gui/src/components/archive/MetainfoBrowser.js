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
import React, {useMemo, useEffect, useRef, useLayoutEffect, useContext, useState, useCallback} from 'react'
import PropTypes from 'prop-types'
import { useRecoilValue, useRecoilState, atom } from 'recoil'
import { configState } from './ArchiveBrowser'
import Browser, { Item, Content, Compartment, Adaptor, laneContext, Title, ItemChip } from './Browser'
import { Typography, Box, makeStyles, FormGroup, TextField, Button, Link, Tooltip, IconButton } from '@material-ui/core'
import ClipboardIcon from '@material-ui/icons/Assignment'
import { vicinityGraph, SubSectionMDef, SectionMDef, QuantityMDef, CategoryMDef, useGlobalMetainfo, PackageMDef, AttributeMDef, getMetainfoFromDefinition } from './metainfo'
import * as d3 from 'd3'
import blue from '@material-ui/core/colors/blue'
import teal from '@material-ui/core/colors/teal'
import lime from '@material-ui/core/colors/lime'
import purple from '@material-ui/core/colors/purple'
import grey from '@material-ui/core/colors/grey'
import Markdown from '../Markdown'
import Histogram from '../Histogram'
import { docsBase } from '../../config'
import { useHistory, useRouteMatch } from 'react-router-dom'
import Autocomplete from '@material-ui/lab/Autocomplete'
import { useApi } from '../api'
import { useErrors } from '../errors'
import { SourceJsonDialogButton } from '../buttons/SourceDialogButton'
import ReactJson from 'react-json-view'
import CopyToClipboard from 'react-copy-to-clipboard'
import ArchiveSearchBar from './ArchiveSearchBar'
import {getDisplayLabel} from "../../utils"

export const help = `
The NOMAD *metainfo* defines all quantities used to represent archive data in
NOMAD. You could say it is the archive *schema*. You can browse this schema and
all its definitions here.

The NOMAD metainfo contains three different *kinds* of definitions:

- **sections**: A section is a nested groups of quantities that allow a hierarchical data structure
- **values**: Actual quantities that contain data
- **references**: References that allow to connect related sections.

All definitions have a name that you can search for. Furthermore, all definitions
are organized in packages. There is a *common* pkg with definitions that are
used by all codes and there are packages for each code with code specific definitions.
You can select the pkg to browse below.

Depending on the selected pkg, there are quite a large number of definitions.
You can use the *definition* field to search based on definition names.

All definitions are represented as *cards* below. Click on the various card items
to expand sub-sections, open values or references, hide and show compartments, or
collapse cards again. The highlighted *main* card cannot be collapsed. The
shapes in the background represent section containment (grey) and
reference (blue) relations.

If you bookmark this page, you can save the definition represented by the highlighted
*main* card.

To learn more about the metainfo, visit the [metainfo documentation](${docsBase}/reference/glossary.html#metainfo).
`

const showInnerSectionDefinitions = false

function defCompare(a, b) {
  return a.name.localeCompare(b.name)
}

export const metainfoConfigState = atom({
  key: 'metainfoConfig',
  default: {
    'packagePrefix': 'all'
  }
})

export function MetainfoPage() {
  return <Box margin={3}>
    <MetainfoBrowser />
  </Box>
}

export default function MetainfoBrowser() {
  const globalMetainfo = useGlobalMetainfo()
  const adaptor = useMemo(() => {
    return globalMetainfo ? new MetainfoRootAdaptor(globalMetainfo.getEntryArchiveDefinition()) : null
  }, [globalMetainfo])
  if (!adaptor) {
    return null
  }
  return <Browser
    adaptor={adaptor}
    form={<MetainfoConfigForm />}
  />
}

const useStyles = makeStyles(theme => {
  return {
    container: {
      alignItems: 'center',
      flexWrap: 'nowrap'
    },
    searchBar: {
      width: 850,
      marginRight: theme.spacing(1.5)
    },
    sourceBar: {
      width: 350
    },
    textInput: {
      margin: 0
    }
  }
})

const MetainfoConfigForm = React.memo(function MetainfoConfigForm(props) {
  const styles = useStyles()
  const [config, setConfig] = useRecoilState(metainfoConfigState)
  const globalMetainfo = useGlobalMetainfo()
  const history = useHistory()
  const { url } = useRouteMatch()

  const searchOptions = useMemo(() => {
    const defsByName = globalMetainfo.getDefsByName()
    const searchOptions = {}
    globalMetainfo && Object.keys(defsByName).forEach((name) => {
      const defsForName = defsByName[name].filter(def => (
        !def.extends_base_section && def.m_def !== SubSectionMDef && (
          def._package.name.startsWith(config.packagePrefix) || def._package.name.startsWith('nomad'))
      ))
      defsForName.forEach(def => {
        const path = globalMetainfo.path(def)
        const prefix = 'nomad.datamodel.datamodel.EntryArchive/'
        const instance = path.startsWith(prefix)
        let info, label
        if (instance) {
          info = path.slice(prefix.length, path.length)?.replace(/\//g, ".")
          label = info.split(".").slice(-1)[0] || info
        } else {
          info = def?._qualifiedName?.split('/')[0]
          label = def.more?.label || def.name
        }
        const key = info || label
        const option = {
          url: url + '/' + path,
          primary: label,
          secondary: info,
          key: key
        }
        searchOptions[key] = option
      })
    })
    return searchOptions
  }, [config.packagePrefix, url, globalMetainfo])

  const handleChange = useCallback((path) => {
    if (path) {
      history.push(path)
    }
  }, [history])

  return (
    <Box marginBottom={1.5} padding={0}>
      <FormGroup row className={styles.container}>
        <ArchiveSearchBar
          options={searchOptions}
          onChange={handleChange}
          className={styles.searchBar}
        />
        <Autocomplete
          className={styles.sourceBar}
          value={config.packagePrefix}
          options={globalMetainfo ? ['all', ...Object.keys(globalMetainfo.getPackagePrefixes())] : []}
          getOptionLabel={option => option.replace(/parser/g, '')}
          onChange={(_, value) => setConfig({...config, packagePrefix: value})}
          renderInput={(params) => <TextField
            {...params}
            variant="filled"
            label="source"
            margin="normal"
            className={styles.textInput}
          />}
        />
      </FormGroup>
    </Box>
  )
})

export async function metainfoAdaptorFactory(def) {
  if (def.m_def === SectionMDef) {
    return new SectionDefAdaptor(def)
  } else if (def.m_def === SubSectionMDef) {
    return new SubSectionDefAdaptor(def)
  } else if (def.m_def === QuantityMDef) {
    return new QuantityDefAdaptor(def)
  } else if (def.m_def === CategoryMDef) {
    return new CategoryDefAdaptor(def)
  } else if (def.m_def === PackageMDef) {
    return new PackageDefAdaptor(def)
  } else if (def.m_def === AttributeMDef) {
    return new AttributeDefAdaptor(def)
  } else {
    throw new Error('Unknown metainfo definition type')
  }
}

class MetainfoAdaptor extends Adaptor {
  constructor(def) {
    super()
    this.def = def
  }

  async itemAdaptor(key) {
    if (key === '_reference') {
      return metainfoAdaptorFactory(this.def.type._referencedDefinition)
    } else if (key.startsWith('_category:')) {
      const categoryName = key.split(':')[1]
      return metainfoAdaptorFactory(this.def.categories.find(categoryDef => categoryDef.name === categoryName))
    } else if (key === '_metainfo') {
      return metainfoAdaptorFactory(await getMetainfoFromDefinition(this.def).resolveDefinition(this.def.m_def))
    } else {
      throw new Error('Unknown item key')
    }
  }

  async initialize(api, dataStore) {
    if (this.def.m_def === SectionMDef || this.def.m_def === SubSectionMDef) {
      this.inheritingSections = dataStore.getAllInheritingSections(this.def.sub_section || this.def)
    }
  }
}

export class MetainfoRootAdaptor extends MetainfoAdaptor {
  async itemAdaptor(key) {
    const rootSection = getMetainfoFromDefinition(this.def).getRootSectionDefinitions().find(def => def._qualifiedName === key)
    if (rootSection) {
      return metainfoAdaptorFactory(rootSection)
    } else {
      const packagePrefixes = getMetainfoFromDefinition(this.def).getPackagePrefixes()
      if (packagePrefixes[key]) {
        return new PackagePrefixAdaptor(packagePrefixes[key])
      }
    }

    return super.itemAdaptor(key)
  }
  render() {
    return <Metainfo />
  }
}

export class PackageDefAdaptor extends MetainfoAdaptor {
  async itemAdaptor(key) {
    const sectionDef = this.def.section_definitions.find(sectionDef => sectionDef.name === key)
    if (sectionDef) {
      return metainfoAdaptorFactory(sectionDef)
    }
    return super.itemAdaptor(key)
  }
  render() {
    return <Content>
      <ArchiveTitle def={this.def} />
      <DefinitionDocs def={this.def} />
      <Compartment>
        {this.def.section_definitions.map(sectionDef => (
          <Item key={sectionDef.name} itemKey={sectionDef.name}>
            <Typography>{sectionDef.name}</Typography>
          </Item>
        ))}
      </Compartment>
    </Content>
  }
}

export class PackagePrefixAdaptor extends MetainfoAdaptor {
  async itemAdaptor(key) {
    const [type, name] = key.split('@')
    const def = Object.keys(this.def)
      .map(key => this.def[key])
      .reduce((value, pkg) => value || pkg[type].find(def => def._qualifiedName === name), null)
    return metainfoAdaptorFactory(def)
  }
  render() {
    const sectionDefs = Object.keys(this.def)
      .map(key => this.def[key])
      .reduce((defs, pkg) => {
        pkg.section_definitions.forEach(def => defs.push(def))
        return defs
      }, [])
    const categoryDefs = Object.keys(this.def)
      .map(key => this.def[key])
      .reduce((defs, pkg) => {
        pkg.category_definitions.forEach(def => defs.push(def))
        return defs
      }, [])

    return <Content>
      <Compartment title="Sections">
        {sectionDefs.filter(def => !def.extends_base_section).sort(defCompare).map(def => {
          const key = `section_definitions@${def._qualifiedName}`
          return <Item key={key} itemKey={key}>
            <Typography>{def.more?.label || def.name}</Typography>
          </Item>
        })}
      </Compartment>
      <Compartment title="Section Extensions">
        {sectionDefs.filter(def => def.extends_base_section).sort(defCompare).map(def => {
          const key = `section_definitions@${def._qualifiedName}`
          return <Item key={key} itemKey={key}>
            <Typography>{def.more?.label || def.name}</Typography>
          </Item>
        })}
      </Compartment>
      <Compartment title="Categories">
        {categoryDefs.sort(defCompare).map(def => {
          const key = `category_definitions@${def._qualifiedName}`
          return <Item key={key} itemKey={key}>
            <Typography>{def.more?.label || def.name}</Typography>
          </Item>
        })}
      </Compartment>
    </Content>
  }
}

const Metainfo = function Metainfo(props) {
  const globalMetainfo = useGlobalMetainfo()
  return <Content>
    <Compartment title="archive root section">
      <Item itemKey="nomad.datamodel.datamodel.EntryArchive">
        <Typography>Entry</Typography>
      </Item>
    </Compartment>
    <Compartment title="sources">
      {Object.keys(globalMetainfo.getPackagePrefixes()).map(key => <Item key={key} itemKey={key}>
        <Typography>{key.replace(/parser$/, '')}</Typography>
      </Item>)}
    </Compartment>
    <Compartment title="other root sections" startCollapsed>
      {globalMetainfo.getRootSectionDefinitions().filter(def => def.name !== 'EntryArchive').map((def, i) => (
        <Item key={i} itemKey={def._qualifiedName}>
          <Typography>
            {def.more?.label || def.name}
          </Typography>
        </Item>
      ))}
    </Compartment>
  </Content>
}

export class SectionDefAdaptor extends MetainfoAdaptor {
  itemAdaptor(key) {
    if (key.includes('@')) {
      const [type, name] = key.split('@')
      if (type === '_innerSectionDef') {
        const innerSectionDef = this.def.inner_section_definitions.find(def => def.name === name)
        if (innerSectionDef) {
          return metainfoAdaptorFactory(innerSectionDef)
        }
      } else if (type === '_inheritingSectionDef') {
        const inheritingSectionDef = this.inheritingSections.find(inheritingSection => inheritingSection._qualifiedName === name)
        if (inheritingSectionDef) {
          return metainfoAdaptorFactory(inheritingSectionDef)
        }
      } else if (type === '_baseSectionDef') {
        const baseSectionDef = this.def.base_sections[parseInt(name)]
        if (baseSectionDef) {
          return metainfoAdaptorFactory(baseSectionDef)
        }
      }
    }

    const property = this.def._properties[key]
    if (property) {
      return metainfoAdaptorFactory(property)
    }

    const attribute = this.def._allAttributes?.find(attr => attr.name === key)
    if (attribute) {
      return metainfoAdaptorFactory(attribute)
    }

    return super.itemAdaptor(key)
  }
  render() {
    return <SectionDef def={this.def} inheritingSections={this.inheritingSections}/>
  }
}

class SubSectionDefAdaptor extends MetainfoAdaptor {
  constructor(def) {
    super(def)
    this.sectionDefAdaptor = new SectionDefAdaptor(this.def.sub_section)
  }
  async initialize(api, dataStore) {
    await this.sectionDefAdaptor.initialize(api, dataStore)
  }
  cleanup() {
    this.sectionDefAdaptor.cleanup()
  }
  async itemAdaptor(key) {
    const attributeDef = this.def._allAttributes?.find(def => def.name === key)
    if (attributeDef) {
      return metainfoAdaptorFactory(attributeDef)
    }
    return this.sectionDefAdaptor.itemAdaptor(key)
  }
  render() {
    return <SubSectionDef def={this.def} inheritingSections={this.sectionDefAdaptor.inheritingSections}/>
  }
}

class QuantityDefAdaptor extends MetainfoAdaptor {
  itemAdaptor(key) {
    const attributeDef = this.def._allAttributes?.find(def => def.name === key)
    if (attributeDef) {
      return metainfoAdaptorFactory(attributeDef)
    }

    return super.itemAdaptor(key)
  }

  render() {
    return <QuantityDef def={this.def} />
  }
}

class CategoryDefAdaptor extends MetainfoAdaptor {
  render() {
    return <Content>
      <Definition def={this.def} />
      <DefinitionDetails def={this.def} />
    </Content>
  }
}

class AttributeDefAdaptor extends MetainfoAdaptor {
  render() {
    return <AttributeDef def={this.def} />
  }
}

function SectionDefContent({def, inheritingSections}) {
  const config = useRecoilValue(configState)
  const metainfoConfig = useRecoilValue(metainfoConfigState)
  const filter = def.extends_base_section ? () => true : def => {
    if (def?._package?._unique_id?.startsWith('entry_id:')) {
      // dynamically loaded custom schema
      return true
    }
    if (def._package.name.startsWith('nomad')) {
      return true
    }
    if (def._package.name.startsWith('nexus')) {
      return true
    }
    if (metainfoConfig.packagePrefix !== 'all') {
      return def._package.name.startsWith(metainfoConfig.packagePrefix)
    }
    if (def.name.startsWith('x_')) {
      return config.showCodeSpecific
    }
    return true
  }

  return <React.Fragment>
    <DefinitionProperties def={def} />
    {def.base_sections.length > 0 &&
      <Compartment title="base section">
        {def.base_sections.map((baseSection, index) => {
          const key = `_baseSectionDef@${index}`
          const categories = baseSection.categories
          const unused = categories?.find(c => c.name === 'Unused')
          return <Item key={key} itemKey={key}>
            <Box component="span" whiteSpace="nowrap">
              <Typography component="span" color={unused && 'error'}>
                <Box fontWeight="bold" component="span">
                  {baseSection.name}
                </Box>
              </Typography>
            </Box>
          </Item>
        })}
      </Compartment>
    }
    {inheritingSections.length > 0 &&
      <Compartment title="all inheriting sections" startCollapsed>
        {inheritingSections.map((inheritingSection, index) => {
          const key = `_inheritingSectionDef@${inheritingSection._qualifiedName}`
          const categories = inheritingSection.categories
          const unused = categories?.find(c => c.name === 'Unused')
          return <Item key={index} itemKey={key}>
            <Box component="span" whiteSpace="nowrap">
              <Typography component="span" color={unused && 'error'}>
                <Box fontWeight="bold" component="span">
                  {inheritingSection.name}
                </Box>
              </Typography>
            </Box>
          </Item>
        })}
      </Compartment>
    }
    <Compartment title="sub section definitions">
      {def._allProperties.filter(prop => prop.m_def === SubSectionMDef).filter(filter)
        .map(subSectionDef => {
          const key = subSectionDef.name
          const categories = subSectionDef.categories
          const unused = categories?.find(c => c.name === 'Unused')
          const inherited = subSectionDef._parent !== def
          return <Item key={key} itemKey={key}>
            <Typography component="span" color={unused && 'error'}>
              <Box fontWeight="bold" component="span">
                {subSectionDef.name}
              </Box>
              {subSectionDef.repeats && <ItemChip label="repeats"/>}
              {subSectionDef._overwritten && <ItemChip label="overwritten" />}
              {inherited && <ItemChip label="inherited" />}
            </Typography>
          </Item>
        })
      }
    </Compartment>
    <Compartment title="quantity definitions">
      {def._allProperties.filter(prop => prop.m_def === QuantityMDef).filter(filter)
        .map(quantityDef => {
          const key = quantityDef.name
          const categories = quantityDef.categories
          const unused = categories?.find(c => c.name === 'Unused')
          const inherited = quantityDef._parent !== def
          return <Item key={key} itemKey={key}>
            <Box component="span" whiteSpace="nowrap">
              <Typography component="span" color={unused && 'error'}>
                <Box fontWeight="bold" component="span">
                  {quantityDef.name}
                </Box>
              </Typography>
              {quantityDef._overwritten && <ItemChip label="overwritten" />}
              {inherited && <ItemChip label="inherited" />}
            </Box>
          </Item>
        })
      }
    </Compartment>
    {showInnerSectionDefinitions && <Compartment title="inner section definitions">
      {def.inner_section_definitions.filter(filter)
        .map(innerSectionDef => {
          const key = `_innerSectionDef@${innerSectionDef.name}`
          const categories = innerSectionDef.categories
          const unused = categories?.find(c => c.name === 'Unused')
          return <Item key={key} itemKey={key}>
            <Box component="span" whiteSpace="nowrap">
              <Typography component="span" color={unused && 'error'}>
                <Box fontWeight="bold" component="span">
                  {getDisplayLabel(innerSectionDef, true)}
                </Box>
              </Typography>
            </Box>
          </Item>
        })
      }
    </Compartment>}
    <DefinitionDetails def={def} />
    <Attributes def={def}/>
    <Annotations def={def}/>
  </React.Fragment>
}
SectionDefContent.propTypes = ({
  def: PropTypes.object,
  inheritingSections: PropTypes.array
})

function SectionDef({def, inheritingSections}) {
  const label = getDisplayLabel(def, true)
  return <Content>
    <Definition def={def} kindLabel="section definition" />
    <DefinitionProperties def={def}>
      <Typography>
        <b>label</b>:&nbsp;
        {label}
      </Typography>
    </DefinitionProperties>
    <SectionDefContent def={def} inheritingSections={inheritingSections}/>
  </Content>
}
SectionDef.propTypes = ({
  def: PropTypes.object,
  inheritingSections: PropTypes.array
})

function SubSectionDef({def, inheritingSections}) {
  const sectionDef = def.sub_section
  const label = getDisplayLabel(def, true)
  return <React.Fragment>
    <Content>
      <ArchiveTitle def={def} useName isDefinition kindLabel="sub section definition" />
      <DefinitionProperties def={def}>
        <Typography>
          <b>label</b>:&nbsp;
          {label}
        </Typography>
        <Typography>
          <b>repeats</b>:&nbsp;
          {def.repeats ? 'True' : 'False'}
        </Typography>
      </DefinitionProperties>
      <DefinitionDocs def={sectionDef} />
      <Attributes def={def}/>
      <Annotations def={def}/>
      <SectionDefContent def={sectionDef} inheritingSections={inheritingSections}/>
    </Content>
  </React.Fragment>
}
SubSectionDef.propTypes = ({
  def: PropTypes.object,
  inheritingSections: PropTypes.array
})

function DefinitionProperties({def, children}) {
  const searchAnnotations = def.m_annotations && Object.keys(def.m_annotations)
    .filter(key => key === 'elasticsearch')
    .map(key => def.m_annotations[key].filter(
      value => !(value.endsWith('.suggestion') || value.endsWith('__suggestion')))
    )
  const hasSearchAnnotations = searchAnnotations && searchAnnotations.length > 0

  if (!(children || def.aliases?.length || def.deprecated || (def.more && Object.keys(def.more).length) || hasSearchAnnotations)) {
    return null
  }

  return <Compartment title="properties">
    {children}
    {def.aliases?.length && <Typography><b>aliases</b>:&nbsp;{def.aliases.map(a => `"${a}"`).join(', ')}</Typography>}
    {def.deprecated && <Typography><b>deprecated</b>: {def.deprecated}</Typography>}
    {Object.keys(def.more || {}).map((moreKey, i) => (
      <Typography key={i}><b>{moreKey}</b>:&nbsp;{String(def.more[moreKey])}</Typography>
    ))}
    {hasSearchAnnotations > 0 && <Typography><b>search&nbsp;keys</b>:&nbsp;{
      searchAnnotations.join(', ')}</Typography>}
  </Compartment>
}
DefinitionProperties.propTypes = ({
  def: PropTypes.object,
  children: PropTypes.any
})

function QuantityDef({def}) {
  const label = getDisplayLabel(def, true)
  return <Content>
    <Definition def={def} kindLabel="quantity definition"/>
    <DefinitionProperties def={def}>
      <Typography>
        <b>label</b>:&nbsp;
        {label}
      </Typography>
      {def.type.type_kind !== 'reference'
        ? <Typography>
          <b>type</b>:&nbsp;
          {Array.isArray(def.type.type_data) ? def.type.type_data.join(', ') : def.type.type_data}&nbsp;
          {def.type.type_kind !== 'data' && `(${def.type.type_kind})`}
        </Typography>
        : <Item itemKey="_reference">
          <Typography>
            <b>referenced section</b>
          </Typography>
        </Item>}
      <Typography>
        <b>shape</b>:&nbsp;
        [{def.shape.join(', ')}]
      </Typography>
      {def.derived && <Typography><b>repeats</b>:&nbsp;true</Typography>}
      {def.unit &&
        <Typography><b>unit</b>:&nbsp;{def.unit}</Typography>}
      {def.dimensionality &&
        <Typography><b>dimensionality</b>:&nbsp;{def.dimensionality}</Typography>}
      {def.default &&
        <Typography><b>default</b>:&nbsp;{String(def.default)}</Typography>}
      {def.derived && <Typography><b>derived</b>:&nbsp;true</Typography>}
      {def.variable && <Typography><b>variable</b>:&nbsp;true</Typography>}
    </DefinitionProperties>
    <Attributes def={def}/>
    <Annotations def={def}/>
  </Content>
}
QuantityDef.propTypes = ({
  def: PropTypes.object
})

function AttributeDef({def}) {
  return <Content>
    <Definition def={def} kindLabel="attribute definition"/>
    <DefinitionProperties def={def}>
      <Typography>
        <b>type</b>:&nbsp;
        {Array.isArray(def.type.type_data) ? def.type.type_data.join(', ') : def.type.type_data}&nbsp;
        {def.type.type_kind !== 'data' && `(${def.type.type_kind})`}
      </Typography>
      {def.shape && <Typography>
        <b>shape</b>:&nbsp;
        [{def.shape.join(', ')}]
      </Typography>}
      {def.default &&
        <Typography><b>default</b>:&nbsp;{String(def.default)}</Typography>}
    </DefinitionProperties>
    <Attributes def={def}/>
    <Annotations def={def}/>
  </Content>
}
AttributeDef.propTypes = ({
  def: PropTypes.object
})

function Attributes({def}) {
  if (!def._allAttributes?.length) {
    return null
  }

  return <Compartment title="attributes">
     {def._allAttributes.map((attributeDef) => {
        const key = attributeDef.name
        const inherited = attributeDef._parent !== def
        return <Item key={key} itemKey={attributeDef.name}>
          <Typography component="span">
            <Box fontWeight="bold" component="span">
              {attributeDef.more?.label || attributeDef.name}
            </Box>
            {attributeDef._overwritten && <ItemChip label="overwritten" />}
            {inherited && <ItemChip label="inherited" />}
          </Typography>
        </Item>
      })}
  </Compartment>
}
Attributes.propTypes = ({
  def: PropTypes.object
})

function DefinitionDocs({def}) {
  return <React.Fragment>
    {def.description && !def.extends_base_section &&
      <Compartment title="description">
        <Box marginTop={1} marginBottom={1}>
          {def?._qualifiedName?.startsWith('nexus')
            ? <Typography>{def.description}</Typography>
            : <Markdown>{def.description}</Markdown>}
        </Box>
      </Compartment>
    }
    {def.links?.length &&
      <Compartment title="links">
        {def.links.map((link, i) => <div key={i}>
          <Link href={link} key={i}>{link.includes('manual.nexusformat.org') ? 'nexus manual' : link}</Link>
        </div>)}
      </Compartment>
    }
  </React.Fragment>
}
DefinitionDocs.propTypes = {
  def: PropTypes.object.isRequired
}

function Definition({def, ...props}) {
  return <React.Fragment>
    <ArchiveTitle def={def} useName isDefinition {...props} />
    <DefinitionDocs def={def} />
  </React.Fragment>
}
Definition.propTypes = {
  def: PropTypes.object.isRequired
}

function DefinitionDetails({def, ...props}) {
  const {api} = useApi()
  const {raiseError} = useErrors()
  const lane = useContext(laneContext)
  const [usage, setUsage] = useState(null)
  const [showUsage, setShowUsage] = useState(false)

  const quantityPath = useMemo(() => {
    const path = lane.path.split('/')
    const index = path.indexOf('EntryArchive')
    if (index >= 0) {
      return path.slice(index + 1).join('.')
    }
    return null
  }, [lane])

  useEffect(() => {
    if (showUsage) {
      api.post('/entries/query', {
        owner: 'visible',
        query: {
          'quantities:any': [quantityPath]
        },
        aggregations: {
          program_names: {
            terms: {
              quantity: 'results.method.simulation.program_name',
              pagination: {
                page_size: 100 // make sure we get all codes
              }
            }
          }
        }
      }).then(response => {
        const aggData = response.aggregations.program_names.terms.data
        setUsage(aggData)
      }).catch(raiseError)
    }
  }, [api, raiseError, showUsage, quantityPath, setUsage])

  return <React.Fragment>
    {def.categories && def.categories.length > 0 && <Compartment title="Categories">
      {def.categories.map((categoryDef, index) => (
        <Item key={index} itemKey={'_category:' + categoryDef.name}>
          <Typography>{categoryDef.more?.label || categoryDef.name}</Typography>
        </Item>
      ))}
    </Compartment>}
    {/* {!lane.next && !def.extends_base_section && def.name !== 'EntryArchive' &&
      <Compartment title="graph">
        <VicinityGraph def={def} key={def.name}/>
      </Compartment>
    } */}
    {quantityPath &&
      <Compartment title="usage">
        {!showUsage && <Button fullWidth variant="outlined" onClick={() => setShowUsage(true)}>Show usage</Button>}
        {showUsage && !usage && <Typography><i>loading ...</i></Typography>}
        {usage && usage.length > 0 && (
          <Histogram
            data={usage.map(use => ({
              key: use.value,
              name: use.value,
              value: use.count
            }))}
            initialScale={0.5}
            title="Metadata use per code"
          />
        )}
        {usage && usage.length === 0 && (
          <Typography color="error"><i>This metadata is not used at all.</i></Typography>
        )}
      </Compartment>
    }
  </React.Fragment>
}
DefinitionDetails.propTypes = {
  def: PropTypes.object.isRequired
}

const definitionLabels = {
  [PackageMDef]: 'package',
  [SectionMDef]: 'section',
  [QuantityMDef]: 'quantity',
  [SubSectionMDef]: 'sub section',
  [CategoryMDef]: 'category',
  [AttributeMDef]: 'attribute'
}

export function ArchiveTitle({def, property, isDefinition, data, kindLabel, useName, actions}) {
  const color = isDefinition ? 'primary' : 'initial'
  let label = definitionLabels[def.m_def]
  if (def.extends_base_section) {
    label += ' extension'
  }
  const title = isDefinition || useName ? def.name : getDisplayLabel(def)
  const m_def = def._qualifiedName || def.name

  return (
    <>
      <Title
        title={title}
        label={`${label}${isDefinition ? ' definition' : ''}`}
        color={color}
        definitionName={!isDefinition && def.name}
        subSectionName={!isDefinition && property && property.m_def === 'nomad.metainfo.metainfo.SubSection' && property.name}
        actions={actions || (
          <SourceJsonDialogButton
            buttonProps={{ size: 'small' }}
            tooltip={`Show ${(kindLabel + ' ') || ''}data as JSON`}
            title={`Underlying ${(kindLabel + ' ') || ''}data as JSON`}
            data={data || def}
          />
        )}
      />
      <Box display='flex' width='100%' maxWidth='300px' alignItems='center'>
        <Box flex={1} minWidth={0}>
          <Tooltip title={m_def}>
            <Typography style={{overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              <b>m_def:&nbsp;</b>{m_def}
            </Typography>
          </Tooltip>
        </Box>
        <CopyToClipboard text={m_def}>
          <Tooltip title="Copy to clipboard">
            <IconButton size='small'>
              <ClipboardIcon />
            </IconButton>
          </Tooltip>
        </CopyToClipboard>
      </Box>
    </>
  )
}
ArchiveTitle.propTypes = ({
  def: PropTypes.object.isRequired,
  property: PropTypes.object,
  data: PropTypes.any,
  isDefinition: PropTypes.bool,
  kindLabel: PropTypes.string,
  useName: PropTypes.bool,
  actions: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ])
})

export function DefinitionLabel({def, isDefinition, ...props}) {
  let label = definitionLabels[def.m_def]
  if (def.extends_base_section) {
    label += ' extension'
  }
  return <Typography {...props}>
    {label}{isDefinition ? ' definition' : ''}
  </Typography>
}
DefinitionLabel.propTypes = ({
  def: PropTypes.object.isRequired,
  isDefinition: PropTypes.bool
})

const Annotations = React.memo(function Annotations({def}) {
  if (!def.m_annotations) {
    return null
  }

  return (
    <Compartment title="annotations">
      <ReactJson
        name="m_annotations"
        src={def.m_annotations}
        enableClipboard={false}
        collapsed={2}
        displayObjectSize={false}
      />
    </Compartment>
  )
})
Annotations.propTypes = {
  def: PropTypes.object.isRequired
}

const useVicinityGraphStyles = makeStyles(theme => ({
  root: {
    with: '100%',
    minWidth: 300,
    '& .links line': {
      // stroke: '#000'
      strokeWidth: 3
    },
    '& text': {
      fontFamily: 'Titillium Web, sans',
      fontSize: 12,
      fontWeight: 'bold'
    }
  }
}))

const linkColors = {
  [QuantityMDef]: purple[500],
  '_none': grey[700]
}
const nodeColors = {
  [QuantityMDef]: lime[500],
  [SectionMDef]: blue[500],
  [CategoryMDef]: teal[500]
}

export function VicinityGraph({def}) {
  const globalMetainfo = useGlobalMetainfo()

  const graph = useMemo(() => {
    const graph = vicinityGraph(def)
    let x1 = Math.min(...graph.nodes.map(n => n.x)) - 32
    const y1 = Math.min(...graph.nodes.map(n => n.y)) - 24
    let x2 = Math.max(...graph.nodes.map(n => n.x)) + 32
    const y2 = Math.max(...graph.nodes.map(n => n.y)) + 24
    const w = Math.max(200, Math.abs(x2 - x1))
    const px = w < 400 ? (400 - w) / 2 : 0
    x1 -= px; x2 += px
    graph.viewBox = `${x1} ${y1} ${x2 - x1} ${y2 - y1}`
    graph.aspectRatio = Math.abs((x2 - x1) / (y2 - y1))

    return graph
  }, [def])

  const classes = useVicinityGraphStyles()
  const svgRef = useRef()
  const history = useHistory()

  useEffect(() => {
    const svg = d3.select(svgRef.current)

    const link = svg.select('.links')
      .selectAll('line')
      .data(graph.links)
      .enter().append('line')
      .attr('marker-end', d => `url(#arrowhead-${d.def.m_def || '_none'})`)
      .attr('stroke', d => linkColors[d.def.m_def || '_none'])
      .on('click', d => {
        if (d.def.m_def === QuantityMDef) {
          const path = globalMetainfo.path(d.def)
          if (path) {
            history.push(`/metainfo/${path}`)
          }
        }
      })

    const node = svg.select('.nodes')
      .selectAll('g')
      .data(graph.nodes)
      .enter().append('g')

    node.append('circle')
      .attr('r', 10)
      .attr('fill', d => nodeColors[d.def.m_def] || '#000')
      .on('click', d => {
        const path = globalMetainfo.path(d.def)
        if (path) {
          history.push(`/metainfo/${path}`)
        }
      })
      .call(d3.drag()
        .on('drag', d => {
          d.x = d3.event.x
          d.y = d3.event.y
          ticked()
        }))

    node.append('text')
      .text(d => d.def.more?.label || d.def.name)
      .attr('text-anchor', 'middle')
      .attr('y', d => ((d.i % 2) === 0) ? 20 : -14)

    function ticked() {
      link
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y)

      node
        .attr('transform', d => 'translate(' + d.x + ',' + d.y + ')')
    }
    ticked()
  }, [graph, history, svgRef, globalMetainfo])

  useLayoutEffect(() => {
    svgRef.current.style.height = svgRef.current.clientWidth / graph.aspectRatio
  }, [graph, svgRef])

  return <svg
    className={classes.root} ref={svgRef}
    viewBox={graph.viewBox}
  >
    <g className='links' />
    <g className='nodes' />
    {Object.keys(linkColors).map(colorKey => {
      const color = linkColors[colorKey]
      return <marker
        key={colorKey}
        id={`arrowhead-${colorKey}`}
        viewBox='-0 -5 10 10'
        refX='21'
        refY='0'
        orient='auto'
        markerWidth='3'
        markerHeight='4'
        xoverflow='visible'
      >
        <path d='M 0,-5 L 10 ,0 L 0,5' fill={color} stroke={color} />
      </marker>
    })}
  </svg>
}
VicinityGraph.propTypes = ({
  def: PropTypes.object.isRequired
})
