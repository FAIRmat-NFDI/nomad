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
import {
  Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogContentText, FormControl, FormControlLabel,
  FormGroup, FormHelperText, Grid, IconButton, makeStyles, MenuItem, TextField, Tooltip, Typography
} from '@material-ui/core'
import grey from '@material-ui/core/colors/grey'
import AddIcon from '@material-ui/icons/AddCircle'
import ArrowDownIcon from '@material-ui/icons/ArrowDropDown'
import NavigateIcon from '@material-ui/icons/ArrowForward'
import ArrowRightIcon from '@material-ui/icons/ArrowRight'
import DownloadIcon from '@material-ui/icons/CloudDownload'
import UploadIcon from '@material-ui/icons/CloudUpload'
import CodeIcon from '@material-ui/icons/Code'
import DeleteIcon from '@material-ui/icons/Delete'
import ReloadIcon from '@material-ui/icons/Replay'
import SaveIcon from '@material-ui/icons/Save'
import { Alert } from '@material-ui/lab'
import classNames from 'classnames'
import DOMPurify from 'dompurify'
import { isArray, isNaN, isPlainObject, partition, range } from 'lodash'
import { complex, format } from 'mathjs'
import PropTypes from 'prop-types'
import React, { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import ReactJson from 'react-json-view'
import { useHistory, useRouteMatch } from 'react-router-dom'
import { atom, useRecoilState, useRecoilValue } from 'recoil'
import { apiBase } from '../../config'
import {
  appendDataUrl, createEntryUrl, createUploadUrl, formatTimestamp,
  getDisplayLabel,
  getOptions,
  isWaitingForUpdateTestId,
  parseNomadUrl, refType, resolveInternalRef,
  resolveNomadUrl,
  resolveNomadUrlNoThrow,
  systemMetainfoUrl, titleCase
} from '../../utils'
import { useApi } from '../api'
import { SourceApiCall, SourceApiDialogButton, SourceJsonDialogButton } from '../buttons/SourceDialogButton'
import { useDataStore, useEntryStoreObj } from '../DataStore'
import { Download } from '../entry/Download'
import { useEntryStore } from '../entry/EntryContext'
import { useErrors } from '../errors'
import Markdown from '../Markdown'
import { EntryButton } from '../nav/Routes'
import { Quantity as Q } from '../units/Quantity'
import { Unit } from '../units/Unit'
import { useDisplayUnit } from '../units/useDisplayUnit'
import H5Web from '../visualization/H5Web'
import Pagination from '../visualization/Pagination'
import ArchiveSearchBar from './ArchiveSearchBar'
import Browser, {
  Adaptor, browserContext, Compartment, Content, Item, ItemChip, laneContext, useLane
} from './Browser'
import { RawFileAdaptor } from './FileBrowser'
import {
  AttributeMDef, getUrlFromDefinition, isEditable, isReference, PackageMDef, QuantityMDef, quantityUsesFullStorage,
  removeSubSection, SectionMDef, SubSectionMDef, useMetainfo
} from './metainfo'
import { ArchiveTitle, DefinitionLabel, metainfoAdaptorFactory } from './MetainfoBrowser'
import { Overview } from './Overview'
import PlotlyFigure from './PlotlyFigure'
import SectionEditor from './SectionEditor'
import { Matrix, Number } from './visualizations'
import XYPlot from "./XYPlot"
import H5WebSectionView, { matchH5Path} from './H5WebSectionView'

export const configState = atom({
  key: 'config',
  default: {
    'showMeta': false,
    'showCodeSpecific': false,
    'showAllDefined': false
  }
})

const ArchiveBrowser = React.memo(function ArchiveBrowser({url}) {
  const parsedUrl = useMemo(() => parseNomadUrl(url), [url])
  const {archive, metadata} = useEntryStoreObj(parsedUrl.deploymentUrl, parsedUrl.entryId, false, '*')
  const metainfo = useMetainfo(systemMetainfoUrl)
  const dataStore = useDataStore()
  const [searchOptions, setSearchOptions] = useState({})
  const rootSectionDef = metainfo ? metainfo.getEntryArchiveDefinition() : null

  useEffect(() => {
    if (!metainfo) return
    let isSubscribed = true
    archiveSearchOptions(archive, metainfo, metadata, dataStore).then((options) => {
      isSubscribed && setSearchOptions(options)
    })
    return () => {
      isSubscribed = false
    }
  }, [archive, metainfo, metadata, dataStore])

  const adaptor = useMemo(() => {
    if (!archive || !rootSectionDef) {
      return null
    }
    return archiveAdaptorFactory(url, archive, rootSectionDef)
  }, [url, archive, rootSectionDef])

  if (!adaptor) {
    return null
  }

  // For some reason, this hook does not work in all of the components used in
  // the Browser (notably: Quantity, QuantityItemPreview). In order to pass the
  // up-to-date unit information, we pass the hook value down the component
  // hierarchy.
  return (
    <Browser
      adaptor={adaptor}
      form={<ArchiveConfigForm searchOptions={searchOptions} data={archive}/>}
    />
  )
})
ArchiveBrowser.propTypes = ({
  url: PropTypes.string.isRequired
})
export default ArchiveBrowser

export const ArchiveSaveButton = React.memo(function ArchiveSaveButton(props) {
  const {editable, archiveHasChanges, saveArchive, reload} = useEntryStore()
  const [openErrorDialog, setOpenErrorDialog] = useState(false)
  const [disabled, setDisabled] = useState(false)

  const handleClick = useCallback(() => {
    saveArchive().catch(error => {
      if (error?.status === 409) {
        setOpenErrorDialog(true)
        setDisabled(true)
      }
    })
  }, [saveArchive])

  const handleReload = useCallback(() => {
    reload()
    setOpenErrorDialog(false)
    setDisabled(false)
  }, [reload])

  const handleClose = useCallback((value) => {
    setOpenErrorDialog(false)
  }, [setOpenErrorDialog])

  return <React.Fragment>
    {editable &&
      <IconButton
        disabled={!archiveHasChanges || disabled} color="primary"
        onClick={handleClick}
      >
        <Tooltip title="Save entry">
          <SaveIcon/>
        </Tooltip>
      </IconButton>
    }
    <Dialog
      open={openErrorDialog}
      aria-describedby="alert-dialog-description"
      onClose={handleClose}
    >
      <DialogContent>
        <DialogContentText>
          The changes cannot be saved. The content has been modified by someone else.
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>OK</Button>
        <Button onClick={handleReload} autoFocus>Reload</Button>
      </DialogActions>
    </Dialog>
  </React.Fragment>
})

export const ArchiveReloadButton = React.memo(function ArchiveReloadButton(props) {
  const {reload} = useEntryStore()

  return <React.Fragment>
    <IconButton
      color="primary"
      onClick={reload}
    >
      <Tooltip title="Reload archive">
        <ReloadIcon/>
      </Tooltip>
    </IconButton>
  </React.Fragment>
})

const useStyles = makeStyles(theme => {
  return {
    container: {
      alignItems: 'center',
      flexWrap: 'nowrap'
    },
    searchBar: {
      width: 750,
      marginRight: theme.spacing(1)
    }
  }
})

const ArchiveConfigForm = React.memo(function ArchiveConfigForm({searchOptions, data}) {
  const styles = useStyles()
  const [config, setConfig] = useRecoilState(configState)
  const history = useHistory()
  const { url } = useRouteMatch()

  const handleConfigChange = event => {
    const changes = {[event.target.name]: event.target.checked}
    if (changes.showCodeSpecific) {
      changes.showAllDefined = !changes.showCodeSpecific
    } else if (changes.showAllDefined) {
      changes.showCodeSpecific = !changes.showAllDefined
    }
    setConfig({...config, ...changes})
  }

  const entryId = data?.metadata?.entry_id

  const handleChange = useCallback((path) => {
    if (path) {
      history.push(url + path)
    }
  }, [history, url])

  return (
    <Box marginBottom={1.5} padding={0}>
      <FormGroup row className={styles.container}>
        <ArchiveSearchBar
          options={searchOptions}
          onChange={handleChange}
          className={styles.searchBar}
          group
        />
        <Box flexGrow={1}/>
        <Tooltip title="Enable to also show all code specific data">
          <FormControlLabel
            control={
              <Checkbox
                checked={config.showCodeSpecific}
                onChange={handleConfigChange}
                name="showCodeSpecific"
              />
            }
            label="code specific"
          />
        </Tooltip>
        <Tooltip title="Enable to also show metadata that is in principle available, but not within this entry">
          <FormControlLabel
            control={
              <Checkbox
                checked={config.showAllDefined}
                onChange={handleConfigChange}
                name="showAllDefined"
              />
            }
            label="all defined"
          />
        </Tooltip>
        <Tooltip title="Show the Metainfo definition on the bottom of each lane">
          <FormControlLabel
            control={
              <Checkbox
                checked={config.showMeta}
                onChange={handleConfigChange}
                name="showMeta"/>
            }
            label="technical view"
          />
        </Tooltip>
        {entryId && <Download
          tooltip="download the archive"
          url={`entries/${entryId}/archive/download`}
          component={IconButton}
        >
          <DownloadIcon/>
        </Download>}
        <SourceApiDialogButton maxWidth="lg" fullWidth>
          <SourceApiCall/>
        </SourceApiDialogButton>
        <ArchiveReloadButton/>
        <ArchiveSaveButton/>
      </FormGroup>
    </Box>
  )
})
ArchiveConfigForm.propTypes = ({
  data: PropTypes.object.isRequired,
  searchOptions: PropTypes.object.isRequired
})

export const ArchiveReUploadButton = React.memo((props) => {
  const {uploadId, metadata, reload} = useEntryStore()
  const {api} = useApi()
  const {raiseError} = useErrors()

  const handleClick = useCallback((files) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.onchange = (event) => {
      const file = event.target.files[0]
      if (!file) {
        return
      }
      const formData = new FormData() // eslint-disable-line no-undef
      formData.set('file', file, metadata.entry_name)
      api.put(`uploads/${uploadId}/raw/?wait_for_processing=true`, formData)
        .then(() => {
          reload()
        })
        .catch(raiseError)
    }

    input.click()
  }, [api, metadata?.entry_name, raiseError, reload, uploadId])

  return <IconButton onClick={handleClick}>
    <Tooltip title="Replace this entry's mainfile">
      <UploadIcon/>
    </Tooltip>
  </IconButton>
})

export function archiveAdaptorFactory(archiveRootUrl, archiveRootObj, rootSectionDef) {
  return new SectionAdaptor(archiveRootUrl, archiveRootObj, rootSectionDef)
}

async function archiveSearchOptions(data, metainfo, metadata, dataStore) {
  const options = {}

  async function traverse(data, sectionDef, parentName, parentPath) {
    let def = sectionDef
    if (data?.m_def) {
      const m_def = data.m_def
      if (def._qualifiedName === 'nomad.datamodel.data.EntryData' || def._qualifiedName === 'nomad.datamodel.datamodel.EntryArchiveReference') {
        const url = metadata.upload_id && metadata.entry_id ? `${apiBase}/uploads/${metadata.upload_id}/archive/${metadata.entry_id}` : undefined
        const sectionDefUrl = resolveNomadUrlNoThrow(m_def, url)
        def = await dataStore.getMetainfoDefAsync(sectionDefUrl)
      }
    }

    for (const key in data) {
      const childDef = def?._properties?.[key]
      if (!childDef) {
        continue
      }

      if (key === "quantities" || key === "base_sections" || key === "sub_sections" || key === 'processing_logs') {
        continue
      }

      if (!(key in data)) {
        continue
      }

      const child = data[key]

      if (child === undefined) {
        continue
      }

      const isSectionDefinitions = key === 'section_definitions'
      const path = parentPath ? (isSectionDefinitions ? parentPath : `${parentPath}/${key}`) : `/${key}`
      const name = parentName ? `${parentName}.${childDef.name}` : childDef.name

      const value = child
      let secondary = childDef?.m_def || ''
      if (typeof value !== 'object' && !Array.isArray(value) && value !== null) {
        secondary = `${value}`
      }
      const pathSections = path.split('/')
      const group = pathSections[0] || pathSections[1]
      options[name] = {
        key: name,
        primary: name,
        secondary: secondary,
        group: group,
        data: data,
        def: childDef,
        url: path
      }

      if (childDef.m_def === SubSectionMDef) {
        const sectionDef = childDef.sub_section
        if (Array.isArray(child) && child.length > 0 && child[0]) {
          if (child.length > 1) {
            for (const [index, value] of child.entries()) {
              await traverse(value, sectionDef, isSectionDefinitions ? `${name}.${value.name}` : name, isSectionDefinitions ? `${path}/${value.name}` : `${path}/${index}`)
            }
          } else {
            await traverse(child[0], sectionDef, name, path)
          }
        } else {
          await traverse(child, sectionDef, name, path)
        }
      }
    }
  }

  await traverse(data, metainfo.getEntryArchiveDefinition(), null, null)
  return new Promise((resolve, reject) => {
    resolve(options)
  })
}

class ArchiveAdaptor extends Adaptor {
  /**
   * @param {*} objUrl An absolute url (string or a parsed url object), identifying a location in some archive.
   * @param {*} obj The data that objUrl points to = a location in some archive.
   * @param {*} def The metainfo definition of obj
   */
  constructor(objUrl, obj, def, property, isInEln) {
    super()
    this.objUrl = objUrl
    this.parsedObjUrl = parseNomadUrl(objUrl)
    if (!this.parsedObjUrl.isResolved) throw new Error(`Resolved url is required, got ${objUrl}`)
    if (this.parsedObjUrl.type !== refType.archive) throw new Error(`Bad url type, expected entry url, got ${objUrl}`)
    // Will be set when initializing the adaptor
    this.api = undefined
    this.dataStore = undefined
    this.entryIsEditable = undefined
    this.isInEln = isInEln === undefined && def.m_def === SectionMDef ? isEditable(def) : isInEln
    this.obj = obj // The data in the archive tree to display
    this.def = def
    this.property = property
    this.external_refs = {}
  }

  async initialize(api, dataStore) {
    this.api = api
    this.dataStore = dataStore
    const {editable, archive: {metadata: {readonly}}} = await dataStore.getEntryAsync(
      this.parsedObjUrl.deploymentUrl, this.parsedObjUrl.entryId, false, '*')
    this.entryIsEditable = editable && !readonly
  }

  async adaptorFactory(objUrl, obj, property) {
    let def = property
    if (property.m_def === SubSectionMDef) {
      def = property.sub_section
    }

    if (obj.m_def === PackageMDef) {
      // We're viewing an archive which contains metainfo definitions, and open the definitions node
      const metainfo = await this.dataStore.getMetainfoAsync(objUrl)
      return metainfoAdaptorFactory(metainfo._data.definitions)
    }

    if (def.m_def === SectionMDef) {
      if (obj.m_def) {
        // Override the def given by the schema with the potentially more specific
        // def given by the data
        // const ref = obj.m_def_id ? `${obj.m_def}@${obj.m_def_id}` : obj.m_def
        const ref = obj.m_def
        const newDefUrl = resolveNomadUrl(ref, objUrl)
        def = await this.dataStore.getMetainfoDefAsync(newDefUrl)
      }
      const isInEln = this.isInEln || isEditable(def)
      return new SectionAdaptor(objUrl, obj, def, property, isInEln)
    }

    if (def.m_def === QuantityMDef) {
      if (def.type.type_kind === 'reference') {
        // Should only happen if the reference encountered has not been resolved, which can
        // happen if the reference is invalid or if it is of a type which we have no handler for.
        return new UnresolvedReferenceAdaptor(objUrl, obj, def)
      }

      return new QuantityAdaptor(objUrl, obj, def)
    }

    if (def.m_def === AttributeMDef) {
      return new AttributeAdaptor(objUrl, obj, def)
    }

    throw new Error('not implemented')
  }

  async itemAdaptor(key) {
    if (key === '_metainfo') {
      return metainfoAdaptorFactory(this.def)
    }

    if (key === '_subsectionmetainfo') {
      return metainfoAdaptorFactory(this.property)
    }

    if (key.startsWith('_external_ref')) {
      const ref = this.external_refs[key]
      if (!ref) return null

      const refUrl = createEntryUrl(apiBase, ref.upload_id, ref.entry_id)
      const {archive} = await this.dataStore.getEntryAsync(apiBase, ref.entry_id, false, '*')
      const metainfo = await this.dataStore.getMetainfoAsync(systemMetainfoUrl)
      const rootSectionDef = metainfo.getEntryArchiveDefinition()
      return this.adaptorFactory(refUrl, archive, rootSectionDef)
    }

    throw new Error('Unknown item key')
  }
}

class SectionAdaptor extends ArchiveAdaptor {
  async itemAdaptor(key) {
    let [name, index] = key.split(':')
    let urlSuffix = index ? `${name}/${index}` : name
    const property = this.def._properties[name] || (name === 'm_attributes' && this.def.attributes.find(attr => attr.name === index))
    let value = this.obj[name] === undefined || this.obj[name] === null ? property?.default : this.obj[name]
    const text_index = index
    index = parseInt(index)
    index = isNaN(index) ? text_index : index
    if (index < 0) index = index + value.length
    if (property.m_def === QuantityMDef && quantityUsesFullStorage(property)) {
      value = value[index]
    }
    if (!property) {
      return super.itemAdaptor(key)
    } else if (property.m_def === SubSectionMDef) {
      let subSectionAdaptor
      let subSectionIndex = -1
      if (property.repeats) {
        subSectionIndex = index || 0
        subSectionAdaptor = await this.adaptorFactory(
          appendDataUrl(this.parsedObjUrl, `${name}/${subSectionIndex}`), value[subSectionIndex], property)
      } else {
        subSectionAdaptor = await this.adaptorFactory(
          appendDataUrl(this.parsedObjUrl, name), value, property)
      }
      subSectionAdaptor.parentRelation = {
        parent: this.obj,
        subSectionDef: property,
        subSectionIndex: subSectionIndex
      }
      return subSectionAdaptor
    } else if (property.m_def === QuantityMDef) {
      // References: sections and quantities
      if (isReference(property)) {
        let reference = null
        if (property.shape.length === 0) {
          reference = value
          urlSuffix = name
        } else if (property.shape.length === 1) {
          const indexStr = key.split(':')[1]
          const index = parseInt(indexStr)
          reference = value[index]
          urlSuffix = `${name}/${index}`
        }
        if (typeof value === 'object' && quantityUsesFullStorage(property)) {
          reference = value?.m_value
        }
        if (!reference) {
          return this.adaptorFactory(
            appendDataUrl(this.parsedObjUrl, urlSuffix), value, property)
        }
        try {
          const resolvedUrl = resolveNomadUrl(reference, this.parsedObjUrl)
          if (resolvedUrl.type === refType.archive) {
            const {archive} = await this.dataStore.getEntryAsync(resolvedUrl.deploymentUrl, resolvedUrl.entryId, false, '*')
            const resolvedObj = resolveInternalRef('/' + (resolvedUrl.path || ''), archive)
            let resolvedDef
            if (resolvedObj.m_def) {
              // the custom definition is present anyway
              resolvedDef = property.type._referencedDefinition
            } else {
              // We need to traverse the data and primarily use definition from
              // the data (m_def), or alternatively load the definition from the
              // metainfo
              let iData = archive
              const metainfo = await this.dataStore.getMetainfoAsync(systemMetainfoUrl)
              let iDef = metainfo.getEntryArchiveDefinition()
              let iSection = 0
              try {
                for (const item of resolvedUrl.path.split('/')) {
                  // Section numbers are stored
                  if (item.match(/^-?\d+$/) || item === '') {
                    iSection = parseInt(item)
                    continue
                  }
                  // If the archive has an m_def, use it
                  iData = iData[isArray(iData) ? iSection : item]
                  if (iData?.m_def) {
                    iDef = await metainfo.resolveDefinition(iData.m_def)
                  // Otherwise use the definition loaded from the parent section
                  } else {
                    iDef = iDef._properties[item]
                    if (iDef.sub_section) iDef = iDef.sub_section
                  }
                }
                resolvedDef = iDef
              // If something goes wrong with the definition resolution, fall
              // back to the type specified in the reference definition.
              } catch {
                resolvedDef = property.type._referencedDefinition
              }
            }
            return this.adaptorFactory(resolvedUrl, resolvedObj, resolvedDef)
          }
          throw new Error('Unhandled reference type')
        } catch (error) {
          // some sections cannot be resolved, because they are not part of the archive
          // user_id->user is one example
          return this.adaptorFactory(
            appendDataUrl(this.parsedObjUrl, urlSuffix), reference, property)
        }
      }
      // Regular quantities
      if (property.m_annotations?.browser) {
        if (property.m_annotations.browser[0].adaptor === 'RawFileAdaptor') {
          if (this.entryIsEditable || !property.shape?.length) {
            const deploymentUrl = this.parsedObjUrl.deploymentUrl
            const uploadId = this.parsedObjUrl.uploadId
            const path = this.obj[property.name]
            const uploadUrl = createUploadUrl(deploymentUrl, uploadId, path, index)
            return new RawFileAdaptor(uploadUrl, null, false)
          }
        }
      }

      if (this.entryIsEditable && property.shape.length === 1 && property?.m_annotations?.eln?.[0] && index !== undefined) {
        return this.adaptorFactory(appendDataUrl(this.parsedObjUrl, urlSuffix), value[index], property)
      }

      return this.adaptorFactory(appendDataUrl(this.parsedObjUrl, urlSuffix), value, property)
    } else if (property.m_def === AttributeMDef) {
      return this.adaptorFactory(
        appendDataUrl(this.parsedObjUrl, `m_attributes/${index}`),
        this.obj?.m_attributes[index],
        property)
    } else {
      throw new Error('Unknown metainfo meta definition')
    }
  }

  render() {
    return <Section
      section={this.obj}
      def={this.def}
      property={this.property}
      parentRelation={this.parentRelation}
      sectionIsInEln={this.isInEln}
      sectionIsEditable={this.entryIsEditable && this.isInEln}
    />
  }
}

class UnresolvedReferenceAdaptor extends ArchiveAdaptor {
  render() {
    return <UnresolvedReference value={this.obj} def={this.def}/>
  }
}

class QuantityAdaptor extends ArchiveAdaptor {
  async itemAdaptor(key) {
    const attribute = this.def?.attributes?.find(attr => attr.name === key)
    if (attribute) {
      const value = this.obj?.m_attributes?.[key]
      return await this.adaptorFactory(
        appendDataUrl(this.parsedObjUrl, `m_attributes/${key}`),
        value,
        attribute)
    }

    return super.itemAdaptor(key)
  }

  render() {
    return <Quantity value={this.obj} def={this.def}>
      {this.obj?.m_attributes?.length > 0 && <Compartment title="attributes">
        {Object.keys(this.obj?.m_attributes).map(key => (
          <Item key={key} itemKey={key}>{key}</Item>
        ))}
      </Compartment>}
    </Quantity>
  }
}

class AttributeAdaptor extends ArchiveAdaptor {
  render() {
    return <Attribute value={this.obj} def={this.def}/>
  }
}

const convertComplexArray = (real, imag) => {
  return Array.isArray(real)
    ? real.map((r, i) => convertComplexArray(r, imag[i]))
    : format(complex(real, imag), {notation: 'auto', precision: 4, lowerExp: -999, upperExp: 999})
}

export function QuantityItemPreview({value, def}) {
  let {finalValue, displayUnit, storageUnit} = useQuantityData(value, def)

  if (isReference(def)) {
    return <Box component="span" fontStyle="italic">
      <Typography component="span">reference ...</Typography>
    </Box>
  }
  if (def.m_annotations?.browser?.[0]?.render_value === 'HtmlValue' || def.m_annotations?.eln?.[0]?.component === 'RichTextEditQuantity') {
    return <Box component="span" whiteSpace="nowrap" fontStyle="italic">
      <Typography component="span">rich text</Typography>
    </Box>
  }
  if (def.type.type_data === 'nomad.metainfo.metainfo._JSON' || def.type.type_data === 'nomad.metainfo.data_type.JSON') {
    return <Box component="span" whiteSpace="nowrap" fontStyle="italic">
      <Typography component="span">JSON data</Typography>
    </Box>
  }
  if (def.type.type_data === 'nomad.datamodel.hdf5.HDF5Dataset' || def.type.type_data === 'nomad.datamodel.hdf5.HDF5Reference') {
    return <Box component="span" fontStyle="italic">
      <Typography component="span">HDF5 array</Typography>
    </Box>
  }
  if (def.shape.length > 0) {
    const dimensions = []
    let typeLabel = 'unknown'
    try {
      let current = finalValue.re || finalValue.im || finalValue
      for (let i = 0; i < def.shape.length; i++) {
        dimensions.push(current.length)
        current = current[0]
      }
      if (def.type.type_kind === 'python') {
        typeLabel = 'list'
      } else if (typeof finalValue === 'string') {
        typeLabel = 'HDF5 array'
        dimensions.length = 0
      } else {
        if (dimensions.length === 1) {
          typeLabel = 'vector'
        } else if (dimensions.length === 2) {
          typeLabel = 'matrix'
        } else {
          typeLabel = 'tensor'
        }
      }
    } catch (e) {
      console.error('Quantity shape did not fit quantity value.', e)
    }
    return <Box component="span" whiteSpace="nowrap" fontStyle="italic">
      <Typography component="span">
        {dimensions.map((dimension, index) => (
          <span key={index}>
            {index > 0 && <span>&nbsp;&times;&nbsp;</span>}{String(dimension)}
          </span>
        ))}&nbsp;{typeLabel}
      </Typography>
    </Box>
  } else {
    if (def.type.type_data === 'nomad.metainfo.metainfo._Datetime' || def.type.type_data === 'nomad.metainfo.data_type.Datetime') {
      finalValue = formatTimestamp(finalValue)
    } else if (def.type.type_data.startsWith?.('complex')) {
      finalValue = convertComplexArray(finalValue.re, finalValue.im)
    }
    if (displayUnit) {
      finalValue = new Q(finalValue, storageUnit).to(displayUnit).value()
    }
    return <Box component="span" whiteSpace="nowarp">
      <Number component="span" variant="body1" value={finalValue} exp={8}/>
      {displayUnit && <Typography component="span">&nbsp;{displayUnit.label()}</Typography>}
    </Box>
  }
}

QuantityItemPreview.propTypes = ({
  value: PropTypes.any,
  def: PropTypes.object.isRequired
})

/**
 * Hook for getting the final value and units for a quantity. Also supports
 * quantities using full storage.
 *
 * @param {*} data Value of the quantity
 * @param {*} def Defintion of the quantity
 * @returns Object containing the final value, storage unit and display unit.
 */
function useQuantityData(data, def) {
  let storageUnit = def.unit
  let displayUnit = useDisplayUnit(def)
  let finalValue = data
  if (quantityUsesFullStorage(def) && isPlainObject(data)) {
    displayUnit = data?.m_unit && new Unit(data.m_unit)
    storageUnit = data?.m_original_unit
    finalValue = data?.m_value
  }
  return {finalValue, displayUnit, storageUnit}
}

export const QuantityValue = React.memo(function QuantityValue({value, def}) {
  const {uploadId} = useEntryStore() || {}
  let {finalValue, displayUnit, storageUnit} = useQuantityData(value, def)

  const getRenderValue = useCallback((value) => {
    let finalValue, finalUnit
    if (def.type.type_data === 'nomad.metainfo.metainfo._Datetime' || def.type.type_data === 'nomad.metainfo.data_type.Datetime') {
      finalValue = formatTimestamp(value)
    } else if (def.type.type_data.startsWith?.('complex')) {
      finalValue = convertComplexArray(value.re, value.im)
    } else {
      finalValue = value
    }

    if (typeof finalValue !== 'string' && storageUnit && displayUnit) {
      const systemUnitQ = new Q(finalValue, storageUnit).to(displayUnit)
      finalValue = systemUnitQ.value()
      finalUnit = systemUnitQ.label()
    }

    return [finalValue, finalUnit]
  }, [def, storageUnit, displayUnit])

  const isMathValue = (def.type.type_kind === 'numpy' || def.type.type_kind === 'python') && typeof value !== 'string'
  if (isMathValue) {
    const [renderValue, finalUnit] = getRenderValue(finalValue)
    if (def.shape.length > 0) {
      return <Box textAlign="center">
        <Matrix
          values={renderValue}
          shape={def.shape}
          invert={def.shape.length === 1}
          type={def.type.type_data}
          key={`matrix:${def.name}`}
        />
        <Typography noWrap variant="caption">
          ({def.shape.map((dimension, index) => <span key={index}>
            {index > 0 && <span>&nbsp;&times;&nbsp;</span>}{String(dimension)}
          </span>)})
        </Typography>
        {finalUnit && <Typography noWrap>{finalUnit}</Typography>}
      </Box>
    } else {
      return <Number value={renderValue} exp={16} variant="body1" unit={finalUnit}/>
    }
  } else if (def.m_annotations?.browser?.[0]?.render_value === 'HtmlValue' || def.m_annotations?.eln?.[0]?.component === 'RichTextEditQuantity') {
    const html = DOMPurify.sanitize(finalValue)
    return <div dangerouslySetInnerHTML={{__html: html}}/>
  } else if (def.type?.type_data === 'nomad.metainfo.metainfo._JSON' || def.type?.type_data === 'nomad.metainfo.data_type.JSON') {
    return <ReactJson
      name="value"
      src={finalValue}
      enableClipboard={false}
      collapsed={2}
      displayObjectSize={false}
    />
  } else {
    if (def.type.type_data.startsWith?.('complex')) {
      finalValue = convertComplexArray(finalValue.re, finalValue.im)

      return Array.isArray(finalValue)
        ? <ul style={{margin: 0}}>
          {finalValue.map((value, index) => <li key={index}><Typography>{value}</Typography></li>)}
        </ul>
        : <Typography>{finalValue}</Typography>
    } else if (Array.isArray(finalValue)) {
      return <ul style={{margin: 0}}>
        {finalValue.map((value, index) => {
          const [renderValue] = getRenderValue(value)
          return <li key={index}>
            <Typography>{typeof renderValue === 'object' ? JSON.stringify(renderValue) : renderValue?.toString()}</Typography>
          </li>
        })}
      </ul>
    } else if (def.type?.type_data === 'nomad.datamodel.hdf5.HDF5Dataset' || def.type?.type_data === 'nomad.datamodel.hdf5.HDF5Reference') {
      const {h5UploadId, h5File, h5Source, h5Path} = matchH5Path(finalValue)
      return <Compartment title='hdf5'>
        <H5Web upload_id={h5UploadId || uploadId} filename={h5File} initialPath={h5Path} source={h5Source} sidebarOpen={false}></H5Web>
      </Compartment>
    } else if (def?.type?.type_kind === 'custom' && def?.type?.type_data === 'nomad.datamodel.data.Query') {
      return <Query value={finalValue} def={def}/>
    } else {
      const [renderValue] = getRenderValue(finalValue)
      return <Typography>{typeof renderValue === 'object' ? JSON.stringify(renderValue) : renderValue?.toString()}</Typography>
    }
  }
})
QuantityValue.propTypes = ({
  value: PropTypes.any,
  def: PropTypes.object.isRequired
})

const InheritingSections = React.memo(function InheritingSections({def, section, lane}) {
  const dataStore = useDataStore()
  const browser = useContext(browserContext)
  const originalInheritingSectionsRef = useRef([def, ...dataStore.getAllInheritingSections(def)])

  const currentInheritingSections = useMemo(() => {
    return [def, ...dataStore.getAllInheritingSections(def)]
  }, [dataStore, def])

  const getSelectionValue = useCallback((def) => {
    return getUrlFromDefinition(def, {deploymentUrl: apiBase}, true)
  }, [])

  const validSections = useMemo(() => {
    const sectionsToSort = section.m_def
        ? currentInheritingSections
        : originalInheritingSectionsRef.current
    return [...sectionsToSort]
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
  }, [currentInheritingSections, section.m_def])
  const showSelection = validSections.length > 1

  const handleInheritingSectionsChange = useCallback((e) => {
    if (Object.keys(section).filter(key => key !== 'm_def').length !== 0) {
      if (!currentInheritingSections.includes(e.target.value)) {
        // TODO show alert dialog
        return
      }
    }
    section.m_def = e.target.value
    browser.invalidateLanesFromIndex(lane.index)
  }, [section, browser, lane, currentInheritingSections])

  if (!showSelection) {
    return null
  }

  return (
    <Box sx={{minWidth: 120}}>
      <FormControl fullWidth>
        <FormHelperText>Multiple specific sections are available</FormHelperText>
        <TextField
          value={getSelectionValue(def)}
          variant="filled"
          label="Select a section"
          data-testid={`inheriting:${def.name}`}
          onChange={handleInheritingSectionsChange}
          size="small"
          select
        >
          {validSections.map((inheritingSection, i) => {
            const sectionValue = getSelectionValue(inheritingSection)
            return (
              <MenuItem key={i} value={sectionValue}>
                {inheritingSection.name}
              </MenuItem>
            )
          })}
        </TextField>
      </FormControl>
    </Box>
  )
})
InheritingSections.propTypes = ({
  section: PropTypes.object.isRequired,
  def: PropTypes.object.isRequired,
  lane: PropTypes.object
})

export function getAllVisibleProperties(sectionDef) {
  const properties = sectionDef?.m_annotations?.eln?.[0]?.properties
  const display = sectionDef?.m_annotations?.display?.[0]
  const hide = sectionDef?.m_annotations?.eln?.[0]?.hide || []
  const allProperties = sectionDef._allProperties?.map(property => property.name)
  const visible = getOptions(display?.visible || properties?.visible, allProperties)
  const editable = getOptions(display?.editable || properties?.editable, allProperties)
  let filteredProperties = sectionDef._allProperties
  filteredProperties = filteredProperties.filter(property => visible.includes(property.name))
  filteredProperties = filteredProperties.filter(property => !hide.includes(property.name))
  for (const property of sectionDef._allProperties || []) {
    const propertyVisible = property?.m_annotations?.display?.[0]?.visible
    if (propertyVisible !== undefined) {
      if (propertyVisible === true) {
        if (!filteredProperties.includes(property)) {
          filteredProperties.push(property)
        }
      } else if (filteredProperties.includes(property)) {
        const index = filteredProperties.indexOf(property)
        if (index > -1) {
          filteredProperties.splice(index, 1)
        }
      }
    }
  }
  const order = display?.order || properties?.order || []
  let visibleProperties = sectionDef._allProperties.filter(property => filteredProperties.includes(property))
  visibleProperties = visibleProperties.map(property => {
    if (display?.editable || properties?.editable) {
      return {...property, _isEditable: editable.includes(property.name)}
    } else {
      return {...property, _isEditable: true}
    }
  })
  visibleProperties = visibleProperties.map((property) => {
    const propertyEditable = property?.m_annotations?.display?.[0]?.editable
    if (propertyEditable !== undefined) {
      return {...property, _isEditable: propertyEditable}
    } else {
      return property
    }
  })
  const reversedOrder = [...order].reverse()
  visibleProperties.sort((a, b) => reversedOrder.indexOf(b.name) - reversedOrder.indexOf(a.name) || a.m_parent_index - b.m_parent_index)
  const quantities = visibleProperties.filter(property => property.m_parent_sub_section === "quantities")
  const sub_sections = visibleProperties.filter(property => property.m_parent_sub_section === "sub_sections")
  return [...quantities, ...sub_sections]
}

export function Section({section, def, property, parentRelation, sectionIsEditable, sectionIsInEln}) {
  const {handleArchiveChanged, uploadId, entryId} = useEntryStore() || {}
  const config = useRecoilValue(configState)
  const [showJson, setShowJson] = useState(false)
  const lane = useContext(laneContext)
  const history = useHistory()

  const isEditable = useMemo(() => {
    let editableExcluded = false
    let parent = def?._parent
    let name = def?.name
    while (!editableExcluded && parent) {
      const properties = parent.m_annotations?.eln?.[0]?.properties
      if (properties && parent._allProperties) {
        const allProperties = parent._allProperties.map(property => property.name)
        const editable = getOptions(properties?.editable, allProperties)
        const excluded = allProperties.filter(propertyName => !editable.includes(propertyName))
        editableExcluded = excluded.some(item => item.toLowerCase() === name.toLowerCase())
      }
      name = parent.name
      parent = parent._parent
    }
    return sectionIsEditable && !editableExcluded
  }, [def, sectionIsEditable])

  const navEntryId = useMemo(() => {
    return lane?.adaptor?.parsedObjUrl?.entryId
  }, [lane])

  const actions = useMemo(() => {
    const navButton = navEntryId && (
      <Grid item>
        <EntryButton entryId={navEntryId} component={IconButton} size="small">
          <NavigateIcon/>
        </EntryButton>
      </Grid>
    )

    const jsonButton = !isEditable ? (
      <Grid item>
        <SourceJsonDialogButton
          buttonProps={{size: 'small'}}
          tooltip={`Show section data as JSON`}
          title={`Underlying section data as JSON`}
          data={section}
          maxWidth="sm"
          fullWidth={true}
        />
      </Grid>
    ) : (
      <Grid item>
        <IconButton onClick={() => setShowJson(value => !value)} size="small">
          <CodeIcon/>
        </IconButton>
      </Grid>
    )

    const handleDelete = () => {
      removeSubSection(
        parentRelation.parent,
        parentRelation.subSectionDef,
        parentRelation.subSectionIndex)
      handleArchiveChanged()
      history.push(lane.prev.path)
    }

    const deleteButton = isEditable && (
      <Grid item>
        <IconButton onClick={handleDelete} size="small">
          <DeleteIcon/>
        </IconButton>
      </Grid>
    )

    return <Grid container justifyContent="space-between" wrap="nowrap" spacing={1}>
      {navButton}{jsonButton}{deleteButton}
    </Grid>
  }, [navEntryId, setShowJson, isEditable, parentRelation, lane, history, handleArchiveChanged, section])

  const renderQuantityItem = useCallback((key, quantityName, quantityDef, value, disabled) => {
    const itemKey = quantityName ? `${key}:${quantityName}` : key
    const isDefault = value !== undefined && value !== null && (section[key] === undefined || section[key] === null)
    const label = getDisplayLabel(quantityDef, true, config?.showMeta)
    return (
      <Box key={itemKey} data-testid={"visible-quantity"}>
        <Item itemKey={itemKey} disabled={disabled}>
          <Box component="span" whiteSpace="nowrap" style={{maxWidth: 100, overflow: 'ellipses'}}>
            <Typography component="span">
              <Box fontWeight="bold" component="span">
                {quantityName || label}
              </Box>
            </Typography>{!disabled &&
            <span>&nbsp;=&nbsp;
              <QuantityItemPreview
                value={value}
                def={quantityDef}
              />
            </span>
          }
          </Box>
          {isDefault && <ItemChip label="default value"/>}
        </Item>
      </Box>
    )
  }, [config?.showMeta, section])

  const renderQuantity = useCallback(quantityDef => {
    const key = quantityDef.name
    const value = section[key] === undefined || section[key] === null ? quantityDef.default : section[key]
    const disabled = value === undefined
    if (!disabled && quantityDef.type.type_kind === 'reference' && quantityDef.shape.length === 1) {
      return <ReferenceValuesList key={key} quantityDef={quantityDef}/>
    }
    if (quantityUsesFullStorage(quantityDef)) {
      const storage = section[quantityDef.name] || {}
      return <React.Fragment key={key}>
        {Object.keys(storage).map(quantityName =>
          renderQuantityItem(key, quantityName, quantityDef, storage[quantityName], disabled)
        )}
      </React.Fragment>
    } else {
      return renderQuantityItem(key, null, quantityDef, value, disabled)
    }
  }, [section, renderQuantityItem])

  const allVisibleProperties = useMemo(() => getAllVisibleProperties(def), [def])

  if (!section) {
    console.error('section is not available')
    return null
  }

  const filter = config.showCodeSpecific ? def => !def.virtual : def => !def.virtual && !def.name.startsWith('x_')
  let sub_sections = allVisibleProperties.filter(prop => prop.m_def === SubSectionMDef)
  if (def.name === 'EntryArchive') {
    // put the most abstract data (last added data) first, e.g. results, metadata, workflow, run
    sub_sections = [...def.sub_sections]
    sub_sections.reverse()
  }
  const quantities = allVisibleProperties.filter(prop => prop.m_def === QuantityMDef)

  const subSectionsToRender = sub_sections
    .filter(subSectionDef => section[subSectionDef.name] || config.showAllDefined || isEditable)
    .filter(filter)
  const subSectionCompartment = (
    <Compartment title="sub sections">
      {subSectionsToRender
        .map(subSectionDef => {
          return <SubSection
            key={subSectionDef.name}
            subSectionDef={subSectionDef}
            section={section}
            editable={isEditable}
          />
        })
      }
    </Compartment>
  )

  let contents
  if (isEditable) {
    contents = <React.Fragment>
      {quantities.length > 0 && (
        <Compartment title="quantities">
          <SectionEditor sectionDef={def} section={section} showJson={showJson}/>
          <Box marginTop={2}>
            {quantities
              .filter(filter)
              .filter(quantityDef => !quantityDef.m_annotations?.eln?.[0]?.component)
              .map(renderQuantity)
            }
          </Box>
        </Compartment>
      )}
      {subSectionCompartment}
      {(def.m_annotations?.plot || def._allBaseSections.map(section => section.name).includes('PlotSection')) && <SectionPlots sectionDef={def} section={section} uploadId={uploadId} entryId={entryId}/>}
      {(def?.m_annotations?.h5web || def._allBaseSections.filter(
        section => section?.m_annotations?.h5web).length > 0) && <H5WebSectionView
          section={section}
          def={def}
          uploadId={uploadId}
          title='hdf5'
        ></H5WebSectionView>}
    </React.Fragment>
  } else {
    const attributes = section?.m_attributes || {}
    contents = <React.Fragment>
      <Compartment title="quantities">
        {quantities
          .filter(quantityDef => section[quantityDef.name] !== undefined || config.showAllDefined || sectionIsInEln)
          .filter(filter)
          .map(renderQuantity)
        }
      </Compartment>
      {subSectionCompartment}
      {Object.keys(attributes).length > 0 && <Compartment title="attributes">
        {Object.keys(attributes).map(key => (
          <Item key={key} itemKey={`m_attributes:${key}`}>{key}</Item>
        ))}
      </Compartment>}
      {(def.m_annotations?.plot || def._allBaseSections.map(section => section.name).includes('PlotSection')) && <SectionPlots sectionDef={def} section={section} uploadId={uploadId} entryId={entryId}/>}
      {(def?.m_annotations?.h5web || def._allBaseSections.filter(
        section => section?.m_annotations?.h5web).length > 0) && <H5WebSectionView
          section={section}
          def={def}
          uploadId={uploadId}
          title='hdf5'
        ></H5WebSectionView>}
    </React.Fragment>
  }
  const eln = def?.m_annotations?.eln
  const laneWidth = (eln && eln.length > 0 ? eln[0].lane_width : undefined)
  const otherProps = (laneWidth ? {minWidth: laneWidth, maxWidth: laneWidth} : undefined)
  return (
    <Content {...otherProps}>
      {isEditable && sectionIsInEln && (
        <InheritingSections def={def} section={section} lane={lane}/>
      )}
      <ArchiveTitle def={def} property={property} data={section} kindLabel="section" actions={actions}/>
      <Overview section={section} def={def}/>
      {contents}
      <ExternalReferences/>
    </Content>
  )
}

Section.propTypes = ({
  section: PropTypes.object.isRequired,
  def: PropTypes.object.isRequired,
  property: PropTypes.object,
  parentRelation: PropTypes.object,
  sectionIsEditable: PropTypes.bool,
  sectionIsInEln: PropTypes.bool
})

export function getItemLabelKey(sectionDef) {
  let itemLabelKey = sectionDef.more?.label_quantity
  if (!itemLabelKey) {
    itemLabelKey = ['name', 'type', 'id'].find(key => (
      sectionDef._properties[key] && sectionDef._properties[key].m_def === QuantityMDef
    ))
  }
  return itemLabelKey
}

function SubSection({subSectionDef, section, editable}) {
  const {handleArchiveChanged} = useEntryStore() || {}
  const lane = useLane()
  const history = useHistory()
  const [open, setOpen] = useState(false)
  const config = useRecoilValue(configState)

  const {itemKey, label, getItemLabel} = useMemo(() => {
    const sectionDef = subSectionDef.sub_section
    let itemLabelKey = getItemLabelKey(sectionDef)
    let labelQuantity = itemLabelKey && sectionDef._properties[itemLabelKey]
    if (labelQuantity && quantityUsesFullStorage(labelQuantity)) {
      // We do not yet support label quantities that use full storage
      labelQuantity = undefined
      itemLabelKey = undefined
    }
    const getItemLabel = item => {
      if (labelQuantity) {
        const value = item[itemLabelKey]
        if (value) {
          return <QuantityValue value={item[itemLabelKey]} def={labelQuantity}/>
        }
      } else if (itemLabelKey) {
        return item[itemLabelKey]
      }
      return null
    }
    return {
      itemKey: subSectionDef.name,
      label: getDisplayLabel(subSectionDef, true, config?.showMeta),
      getItemLabel: getItemLabel
    }
  }, [subSectionDef, config?.showMeta])

  const handleAdd = useCallback(() => {
    let subSectionKey = subSectionDef.name
    if (subSectionDef.repeats) {
      let values = section[subSectionDef.name]
      if (!values) {
        values = []
        section[subSectionDef.name] = values
      }
      values.push({})
      if (values.length > 1) {
        subSectionKey += `:${values.length - 1}`
      }
      setOpen(true)
    } else {
      section[subSectionDef.name] = {}
    }
    handleArchiveChanged()
    history.push(`${lane.path}/${subSectionKey}`)
  }, [subSectionDef, section, lane, history, handleArchiveChanged])

  const values = section[subSectionDef.name] || []
  const showList = subSectionDef.repeats && values.length > 1
  const actions = editable && (subSectionDef.repeats || !section[subSectionDef.name]) && (
    <Box marginRight={!showList && section[subSectionDef.name] ? -1 : 2}>
      <IconButton data-testid={`subsection:${subSectionDef.name}`} onClick={handleAdd} size="small">
        <AddIcon style={{fontSize: 20}}/>
      </IconButton>
    </Box>
  )

  const handleClick = useCallback(() => {
    setOpen(open => !open)
  }, [])

  if (showList) {
    return <PropertyValuesList
      itemKey={itemKey}
      label={label || 'list'}
      actions={actions}
      values={values.map(getItemLabel)}
      open={open}
      onClick={handleClick}
    />
  } else {
    return (
      <Box data-testid={'subsection'}>
        <Item
          itemKey={subSectionDef.repeats ? `${subSectionDef.name}/0` : subSectionDef.name}
          disabled={!section[subSectionDef.name]} actions={actions}
        >
          <Typography component="span">
            <Box fontWeight="bold" component="span">
              {label}
            </Box>
          </Typography>
        </Item>
      </Box>
    )
  }
}

SubSection.propTypes = ({
  subSectionDef: PropTypes.object.isRequired,
  section: PropTypes.object.isRequired,
  editable: PropTypes.bool
})

function ReferenceValuesList({quantityDef}) {
  const lane = useContext(laneContext)
  const values = useMemo(() => lane.adaptor.obj[quantityDef.name].map(() => null), [lane.adaptor.obj, quantityDef.name])
  const [open, setOpen] = useState(false)
  const config = useRecoilValue(configState)
  const label = getDisplayLabel(quantityDef, true, config?.showMeta)

  const handleClick = useCallback(() => {
    setOpen(open => !open)
  }, [])

  return <PropertyValuesList
    itemKey={quantityDef.name}
    values={values}
    label={label}
    open={open}
    onClick={handleClick}
  />
}

ReferenceValuesList.propTypes = ({
  quantityDef: PropTypes.object.isRequired
})

const usePropertyValuesListStyles = makeStyles(theme => ({
  root: {
    margin: `0 -${theme.spacing(1)}px`,
    padding: `0 ${theme.spacing(1)}px`,
    width: '100%',
    display: 'flex',
    flexDirection: 'row',
    wrap: 'nowrap',
    alignItems: 'center'
  },
  title: {
    flexGrow: 1,
    color: theme.palette.text.primary,
    textDecoration: 'none',
    margin: `0 -${theme.spacing(1)}px`,
    whiteSpace: 'nowrap',
    display: 'flex',
    fontWeight: 'bold'
  },
  selected: {
    backgroundColor: theme.palette.primary.main,
    color: theme.palette.primary.contrastText,
    whiteSpace: 'nowrap'
  },
  unSelected: {
    '&:hover': {
      backgroundColor: grey[300]
    }
  },
  actions: {}
}))

/**
 * Displays a list of values that can be collapsed. Long lists are paginated in
 * order to prevent issues with rendering.
 */
export function PropertyValuesList(props) {
  const {label, itemKey, values, ...otherProps} = props
  return <FoldableList
    label={label || itemKey || 'list'}
    {...otherProps}
  >
    {values.map((value, index) => {
      return <Item key={index} itemKey={`${itemKey}/${index}`} length={values.length}>
        <Box display="grid" flexDirection="row" flexGrow={1}>
          <Box component="span" marginLeft={2}>
            {value && typeof value === 'object'
              ? value // item should be a react component
              : <Typography component="span">{value || index}</Typography>
            }
          </Box>
        </Box>
      </Item>
    })}
  </FoldableList>
}

PropertyValuesList.propTypes = ({
  itemKey: PropTypes.string.isRequired,
  label: PropTypes.string,
  values: PropTypes.arrayOf(PropTypes.object).isRequired,
  actions: PropTypes.node,
  /**
   * Initial number of items to be shown from top of the list.
   */
  nTop: PropTypes.number,
  /**
   * Initial number of items to be shown from bottom of the list.
   */
  nBottom: PropTypes.number,
  /**
   * Initial number of items to be added or removed by show more and show less buttons.
   */
  pageSize: PropTypes.number,
  /**
   * To control the collapse state from outside the component.
   */
  open: PropTypes.bool,
  /**
   * An event firing when the item is clicked.
   */
  onClick: PropTypes.func,
  /**
   * To customize the label style in different places.
   */
  className: PropTypes.object,
  /**
   * Get item as children.
   */
  children: PropTypes.node
})

PropertyValuesList.defaultProps = ({
  nTop: 50,
  nBottom: 5,
  pageSize: 25
})

/**
 * Displays a list of values that can be collapsed. Long lists are paginated in
 * order to prevent issues with rendering.
 */
export function FoldableList({label, children, actions, nTop, nBottom, pageSize, open, onClick, className}) {
  const classes = {...usePropertyValuesListStyles(), ...className}
  const [nShownTop, setNShownTop] = useState(0)
  const [nShownBottom, setNShownBottom] = useState(0)
  const lane = useContext(laneContext)
  const selected = lane?.next?.key
  const showSelected = !open && (selected === label || selected?.startsWith(label + ':'))

  const topStart = 0
  const topEnd = Math.min(children.length, nTop + nShownTop)
  const bottomStart = Math.max(topEnd, children.length - nBottom - nShownBottom)
  const bottomEnd = children.length

  return <React.Fragment>
    <div className={classNames(
      classes.root, showSelected ? classes.selected : classes.unSelected)}
    >
      <Typography onClick={onClick} className={classes.title}>
        {open ? <ArrowDownIcon/> : <ArrowRightIcon/>}
        <span role="item-list">{label}</span>
      </Typography>
      {actions && <div className={classes.actions}>
        {actions}
      </div>}
    </div>
    {open &&
      <div data-testid={`item-list:${label}`}>
        {range(topStart, topEnd).map((index) => children[index])}
        {topEnd < bottomStart && <Box marginLeft={0.8}>
          {nTop > 0 && <Pagination
            showMore
            showLess={nShownTop > 0}
            onMore={() => setNShownTop(x => Math.min(bottomStart, x + pageSize))}
            onLess={() => setNShownTop(x => Math.max(0, x - pageSize))}
            variant="down"
            data-testid="propertyvalueslist-pagination-down"
          />}
          {nBottom > 0 && <Pagination
            showMore
            showLess={nShownBottom > 0}
            onMore={() => setNShownBottom(x => Math.min(children.length - nShownTop - nBottom, x + pageSize))}
            onLess={() => setNShownBottom(x => Math.max(0, x - pageSize))}
            variant="up"
            data-testid="propertyvalueslist-pagination-up"
          />}
        </Box>}
        {range(bottomStart, bottomEnd).map((index) => children[index])}
      </div>
    }
  </React.Fragment>
}

FoldableList.propTypes = ({
  label: PropTypes.string.isRequired,
  /**
   * Get item as children.
   */
  children: PropTypes.arrayOf(PropTypes.node).isRequired,
  actions: PropTypes.node,
  /**
   * Initial number of items to be shown from top of the list.
   */
  nTop: PropTypes.number,
  /**
   * Initial number of items to be shown from bottom of the list.
   */
  nBottom: PropTypes.number,
  /**
   * Initial number of items to be added or removed by show more and show less buttons.
   */
  pageSize: PropTypes.number,
  /**
   * To control the collapse state from outside the component.
   */
  open: PropTypes.bool,
  /**
   * An event firing when the item is clicked.
   */
  onClick: PropTypes.func,
  /**
   * To customize the label style in different places.
   */
  className: PropTypes.object
})

FoldableList.defaultProps = ({
  nTop: 50,
  nBottom: 5,
  pageSize: 25
})

export const SectionPlots = React.memo(function SectionPlots({section, sectionDef, uploadId, entryId}) {
  const [selected, setSelected] = useState([])
  const annotationPlot = sectionDef?.m_annotations?.plot && sectionDef.m_annotations?.plot

  const sortedFigures = useMemo(() => {
    let sortedFigures
    if (section?.figures) {
      section?.figures.forEach((figure, index) => {
        figure.pathIndex = index
      })
      const [indexedFigures, otherFigures] = section.figures && partition(section.figures, figure => figure?.index !== undefined)
      sortedFigures = indexedFigures.sort((a, b) => a.index - b.index)
      sortedFigures = [...sortedFigures, ...otherFigures]
    }
    return sortedFigures
  }, [section.figures])

  const plots = useMemo(() => {
    const validPlots = []
    let key = 0
    sortedFigures?.forEach(plot => {
      if (plot?.figure) {
        plot.figure.data = (Array.isArray(plot?.figure?.data) ? [...plot?.figure?.data] : [{...plot?.figure?.data}])
        validPlots.push({pathIndex: plot.pathIndex, plot: plot.figure, key: key, label: plot.label || plot?.layout?.title?.text || 'Untitled', type: 'PlotSection'})
      }
    })
    const annotationPlots = annotationPlot
      ? Array.isArray(annotationPlot) ? [...annotationPlot] : [{...annotationPlot}]
      : undefined
    annotationPlots?.forEach(plot => {
      if (!('label' in plot)) {
        const yAxis = plot.y || plot['y_axis'] || plot['yAxis']
        const pathParts = Array.isArray(yAxis) ? ['unnamed'] : yAxis.split('/')
        plot.label = pathParts[pathParts.length - 1]
      }
      validPlots.push({plot: plot, key: key, label: plot.label || 'Untitled', type: 'PlotAnnotation'})
      key = key + 1
    })
    return validPlots
  }, [annotationPlot, sortedFigures])

  useEffect(() => {
    setSelected(sortedFigures && Array.isArray(sortedFigures) ? sortedFigures.reduce((indices, figure, index) => {
      if (figure?.open === undefined || figure?.open) {
        indices.push(index)
      }
      return indices
    }, []) : [0])
  }, [plots.length, sortedFigures])

  if (plots.length < 1 || selected.find(index => index >= plots.length)) {
    return null
  }

  return <Compartment title="plot">
    <Box minWidth={500}>
      {plots.length > 1 && <TextField
        select variant="filled" size="small" fullWidth label={'Shown plots'} style={{marginBottom: 2}}
        SelectProps={{
          multiple: true,
          value: selected,
          onChange: event => setSelected(event.target.value),
          renderValue: newSelected => newSelected.map(index => {
            return titleCase(plots?.[index]?.label)
          })?.join(', ')
        }}
      >
        {plots.map((plot, index) => (
          <MenuItem key={index} value={index}>
            <Checkbox
              checked={selected.findIndex(selectedIndex => selectedIndex === index) >= 0}
            />
            {titleCase(plots?.[index]?.label)}
          </MenuItem>
        ))}
      </TextField>}
      {selected.map(index => plots?.[index])
        ?.map((plot) => {
          return plot.type === 'PlotSection'
            ? <PlotlyFigure
              key={plot.key}
              sectionDef={sectionDef}
              section={section}
              plot={plot.plot}
              title={plot.label}
              metaInfoLink={uploadId && entryId && `/user/uploads/upload/id/${uploadId}/entry/id/${entryId}/data/data/figures:${plot.pathIndex}/figure`}
            />
            : <XYPlot
              key={plot.key}
              sectionDef={sectionDef}
              section={section}
              plot={plot.plot}
              title={plot.label}
            />
        })}
    </Box>
  </Compartment>
})
SectionPlots.propTypes = {
  sectionDef: PropTypes.object.isRequired,
  section: PropTypes.object,
  uploadId: PropTypes.string,
  entryId: PropTypes.string
}

function Quantity({value, def, children}) {
  const {prev} = useLane()
  return <Content>
    <ArchiveTitle def={def} data={value} kindLabel="value"/>
    {def.m_annotations?.plot && (
      <Compartment title="plot">
        <XYPlot
          section={prev.adaptor.obj}
          sectionDef={prev.adaptor.def}
          plot={def.m_annotations?.plot[0]}
          title={def.name}
        />
      </Compartment>
    )}
    <Compartment title="value">
      <QuantityValue
        value={value}
        def={def}
      />
    </Compartment>
    {children}
    <ExternalReferences/>
  </Content>
}

Quantity.propTypes = ({
  value: PropTypes.any,
  def: PropTypes.object.isRequired,
  unit: PropTypes.string,
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ])
})

function Query({value, def}) {
  return <Content>
    <Compartment title="filters">
      <QuantityValue
        value={value?.filters ? Object.entries(value.filters).map(([key, value]) => `${key}: ${value}`) : []}
        def={def}
      />
    </Compartment>
    {value?.data && <Compartment title="results">
      <QuantityValue
        value={value?.data ? value.data.map(entry => entry.mainfile || entry.entry_id) : []}
        def={def}
      />
    </Compartment>}
    <Compartment title="query">
      <SourceApiCall body={value} response={value}/>
    </Compartment>
  </Content>
}

Query.propTypes = ({
  value: PropTypes.any,
  def: PropTypes.object.isRequired
})

function Attribute({value, def}) {
  return <Content>
    <ArchiveTitle def={def} data={value} kindLabel="attribute"/>
    <Compartment title="value">
      <QuantityValue
        value={value}
        def={def}
      />
    </Compartment>
    <Meta def={def}/>
  </Content>
}

Attribute.propTypes = ({
  value: PropTypes.any,
  def: PropTypes.object.isRequired
})

function UnresolvedReference({value, def}) {
  const refTypeName = def?.type?._referencedDefinition?.name
  const refTypeQualifiedName = def?.type?._referencedDefinition?._qualifiedName
  const isOk = ['nomad.metainfo.metainfo.User'].includes(refTypeQualifiedName) // expected to not be resolvable
  return <Content>
    <ArchiveTitle def={def} data={value} kindLabel="value"/>
    <Compartment title="reference">
      {!isOk && <Typography color="error">Cannot resolve reference.</Typography>}
      <Typography><b>Reference type:</b></Typography>
      <Typography>{refTypeName || 'unknown'}</Typography>
      <Typography><b>Reference value:</b></Typography>
      <Typography>{value}</Typography>
    </Compartment>
    <Meta def={def}/>
  </Content>
}

UnresolvedReference.propTypes = ({
  value: PropTypes.any,
  def: PropTypes.object.isRequired
})

const useMetaStyles = makeStyles(theme => ({
  description: {
    marginTop: theme.spacing(1)
  },
  graph: {
    marginTop: theme.spacing(3)
  },
  metainfo: {
    marginBottom: theme.spacing(2)
  },
  metainfoItem: {
    fontWeight: 'bold'
  }
}))

export function Meta({def}) {
  const classes = useMetaStyles()
  const config = useRecoilValue(configState)
  if (!config.showMeta) {
    return null
  }
  return <Compartment title="meta" color="primary">
    <div className={classes.metainfo}>
      <Item itemKey="_metainfo">
        <DefinitionLabel classes={{root: classes.metainfoItem}} def={def} isDefinition component="span"/>
      </Item>
    </div>
    <Markdown classes={{root: classes.description}}>{def.description}</Markdown>
  </Compartment>
}

Meta.propTypes = ({
  def: PropTypes.object
})

const baseQuery = {
  'exclude': [
    'atoms',
    'only_atoms',
    'files',
    'quantities',
    'dft.quantities',
    'optimade',
    'dft.labels',
    'dft.geometries'
  ],
  'required': {
    'metadata': '*'
  },
  'owner': 'visible',
  'pagination': {
    'order_by': 'upload_create_time', 'order': 'desc', 'page_size': 20
  }
}

function ExternalReferences() {
  const lane = useLane()
  const [searchResults, setSearchResults] = useState(null)

  const {api} = useApi()
  const {raiseError} = useErrors()

  const source = lane.adaptor

  const entryId = source.parsedObjUrl.entryId
  const dataPath = source.parsedObjUrl.path || '/'

  const performSearch = () => {
    if (searchResults) return
    const referencing_query = Object.assign({}, baseQuery, {
      'query': {
        'entry_references.target_entry_id': entryId,
        'entry_references.target_path': dataPath
      }
    })
    api.query('entries', referencing_query, {noLoading: true}).then((data) => {
      source.external_refs = Object.fromEntries(data.data.map((entry, index) => {
        entry.entry_references = entry.entry_references.filter(ref => ref.target_entry_id === entryId && ref.target_path === dataPath)
        return ['_external_ref_' + index, entry]
      }))
      setSearchResults(Object.values(source.external_refs))
    }).catch(raiseError)
  }

  const entry_list = (list) => {
    return list.map((ref, index) => (
      <Item itemKey={'_external_ref_' + index} key={index}><Typography component="span">
        <Box fontWeight="bold" component="span">{ref.mainfile}</Box>
        &nbsp;=&nbsp;
        <Box component="span" fontStyle="italic">entry ...</Box>
        {ref.entry_references?.map(
          (ref, index) => <Typography key={index} variant="body2">{ref.source_path}</Typography>
        )}
      </Typography></Item>
    ))
  }

  return <Compartment title="referenced by" startCollapsed={true} onUnfold={performSearch}>
    {searchResults
      ? searchResults.length > 0
        ? searchResults.length === 20
          ? <Tooltip title="Only showing at most the latest 20 entries.">
            {entry_list(searchResults)}
          </Tooltip>
          : entry_list(searchResults)
        : <Alert severity={'info'}>not referenced by other entries</Alert>
      : <Alert severity={'info'} data-testid={isWaitingForUpdateTestId}>loading...</Alert>}
  </Compartment>
}
