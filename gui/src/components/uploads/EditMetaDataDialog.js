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
import React, {useCallback, useEffect, useMemo, useState} from 'react'
import {
  makeStyles, DialogTitle, DialogContent, Dialog, IconButton, Tooltip, Divider,
  Typography, TextField, Box
} from '@material-ui/core'
import DialogContentText from '@material-ui/core/DialogContentText'
import EditIcon from '@material-ui/icons/Edit'
import Button from '@material-ui/core/Button'
import DialogActions from '@material-ui/core/DialogActions'
import PropTypes from 'prop-types'
import AutoComplete from '@material-ui/lab/Autocomplete'
import {useApi} from '../api'
import {useErrors} from '../errors'
import {useLocation} from 'react-router'
import {Link} from 'react-router-dom'
import {getUrl} from '../nav/Routes'
import {Datatable, DatatableTable} from '../datatable/Datatable'
import DeleteIcon from '@material-ui/icons/Delete'
import OpenInNewIcon from '@material-ui/icons/OpenInNew'
import Quantity from '../Quantity'
import {DOI} from '../DOI'
import { useUploadPageContext } from './UploadPageContext'

function EditComments(props) {
  const {value, onChange} = props

  return <Box display={'block'}>
    <Box marginBottom={1} marginTop={1}>
      <Typography variant='subtitle2'>
        Comments
      </Typography>
    </Box>
    <TextField
      fullWidth label='Comment' multiline
      minRows={6} defaultValue={value} variant='filled' size='small'
      onChange={(event) => onChange(event.target.value)}
      inputProps={{ 'data-testid': 'metadata-comment-field' }}
    />
  </Box>
}
EditComments.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired
}

function EditReferences(props) {
  const {values, onAdd, onRemove} = props
  const [newReference, setNewReference] = useState('')
  const [urlValidation, setUrlValidation] = useState('')

  const validateURL = useCallback((value) => {
    try {
      const url = new URL(value)
      if (url) {}
    } catch (_) {
      return 'Pleas enter a valid URL ...'
    }
  }, [])

  const validation = useMemo(() => {
    if (newReference === '') return undefined
    if (values?.includes(newReference)) return 'The URL is already in the references'
    return undefined
  }, [values, newReference])

  const columns = useMemo(() => {
    return [
      {key: 'url',
        align: 'left',
        render: reference => {
          return <Box maxWidth='300px' whiteSpace='nowrap' textOverflow='ellipsis' overflow='hidden'>
            <Quantity quantity='data' noLabel noWrap data={{data: reference.url}} withClipboard />
          </Box>
        }
      }
    ]
  }, [])

  const handleChangeReference = useCallback((event) => {
    setUrlValidation(undefined)
    setNewReference(event.target.value)
  }, [])

  const handleAdd = useCallback(() => {
    const validateURLError = validateURL(newReference)
    setUrlValidation(validateURLError)
    if (validateURLError) return
    onAdd(newReference)
    setNewReference('')
  }, [newReference, onAdd, validateURL])

  const apiError = useMemo(() => values && values.find(value => !!(value?.error_reference))?.error_reference, [values])

  return <Box display={'block'}>
    <Box marginBottom={1} marginTop={3}>
      <Typography variant='subtitle2'>
        References
      </Typography>
    </Box>
    <TextField
      style={{width: '100%'}} label='Enter the URL' onChange={handleChangeReference}
      error={!!(validation || urlValidation)} helperText={validation || urlValidation} variant='filled' size='small' value={newReference}
      inputProps={{ 'data-testid': 'new-reference-field' }}
    />
    <Box display="flex" justifyContent="right" marginY={1}>
      <Button variant="contained" color="primary" onClick={handleAdd} disabled={!!(validation || newReference === '')} data-testid='reference-add-button'>
        add
      </Button>
    </Box>
    {columns && values.length > 0 && <React.Fragment>
      {apiError && <Typography color="error" role='reference-api-error'>
        {apiError}
      </Typography>}
      <Divider />
      <Datatable columns={columns} data={values.filter(value => !(value?.error_reference)).map(value => Object({url: value, onRemove: onRemove}))}>
        <DatatableTable actions={ReferencesActions} noHeader />
      </Datatable>
    </React.Fragment>}
  </Box>
}
EditReferences.propTypes = {
  values: PropTypes.arrayOf(PropTypes.string).isRequired,
  onAdd: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired
}

const ReferencesActions = React.memo((props) => {
  const {data} = props

  const handleOpenLink = () => {
    window.open(data.url, '_blank')
  }

  return <Box display={'inline-block'}>
    <IconButton size='small' onClick={handleOpenLink}>
      <Tooltip title='Open in new tab'>
        <OpenInNewIcon />
      </Tooltip>
    </IconButton>
    <IconButton size='small' onClick={() => data.onRemove(data.url)} data-testid='reference-delete-action'>
      <Tooltip title='Remove the reference'>
        <DeleteIcon />
      </Tooltip>
    </IconButton>
  </Box>
})
ReferencesActions.propTypes = {
  data: PropTypes.object.isRequired
}

function EditDatasets(props) {
  const {values, userDatasets, onCreate, onAdd, onRemove} = props
  const [newDataset, setNewDataset] = useState('')
  const [datasetToAdd, setDatasetToAdd] = useState('')

  const columns = useMemo(() => ([
    {key: 'dataset', align: 'left', render: row => ('doi' in row.dataset ? <span> {`${row.dataset.dataset_name},  DOI:`} <DOI doi={row.dataset.doi} /></span> : row.dataset.dataset_name)}
  ]), [])

  const isDuplicated = useMemo(() => {
    return !!values.find(dataset => dataset.dataset_id && dataset.dataset_id === datasetToAdd.dataset_id)
  }, [datasetToAdd, values])

  const validation = useMemo(() => {
    if (userDatasets.map(dataset => dataset.dataset_name).includes(newDataset)) return `There is already a dataset with name ${newDataset}`
    if (values.map(dataset => dataset.dataset_name).includes(newDataset)) return `There is already a dataset with name ${newDataset}`
    if (newDataset[0] === ' ') return `Invalid name for dataset`
    return ''
  }, [newDataset, values, userDatasets])

  const handleDatasetToAddChanged = useCallback((event, value) => {
    (value && value?.dataset_id ? setDatasetToAdd(value) : setDatasetToAdd(''))
  }, [])

  const handleNewDatasetChanged = useCallback((event) => {
    setNewDataset(event.target.value)
  }, [])

  const handleCreate = useCallback(() => {
    onCreate(newDataset)
    setNewDataset('')
  }, [newDataset, onCreate])

  const handleAdd = useCallback(() => {
    onAdd(datasetToAdd)
    setDatasetToAdd('')
  }, [datasetToAdd, onAdd])

  const addDatasetButton = useMemo(() => {
    if (validation === '' && newDataset !== '') {
      return <Button color="primary" variant="contained" onClick={handleCreate}>
        add entry to new dataset
      </Button>
    } else if (!isDuplicated && datasetToAdd !== '') {
      return <Button variant="contained" color="primary" onClick={handleAdd}>
        add entry to existing dataset
      </Button>
    } else {
      return <Button color="primary" variant="contained" disabled>add</Button>
    }
  }, [datasetToAdd, newDataset, handleAdd, handleCreate, validation, isDuplicated])

  const apiError = useMemo(() => values && values.find(value => !!(value?.error_dataset))?.error_dataset, [values])

  return <Box display={'block'}>
    <Box marginBottom={1} marginTop={3}>
      <Typography variant='subtitle2'>
        Datasets
      </Typography>
    </Box>
    <Box marginBottom={1}>
      <TextField
        value={newDataset} onChange={handleNewDatasetChanged}
        style={{width: '100%'}} label='Create a new dataset' placeholder='Dataset name'
        error={validation !== ''} helperText={(validation !== '' && validation)}
        variant='filled' size='small'
        inputProps={{ 'data-testid': 'new-dataset-field' }}
      />
    </Box>
    <AutoComplete
      style={{width: '100%'}}
      options={userDatasets}
      getOptionLabel={option => option.dataset_name || ''}
      onChange={handleDatasetToAddChanged}
      value={datasetToAdd}
      getOptionSelected={(option, value) => (value ? option?.dataset_name === value?.dataset_name : true)}
      renderInput={params => (
        <TextField
          {...params}
          variant='filled' label='Search for an existing dataset'
          placeholder='Dataset name' margin='normal' fullWidth size='small'
          error={isDuplicated || (datasetToAdd.dataset_name && validation.value === datasetToAdd.dataset_name)}
          helperText={(isDuplicated ? 'The data is already in the selected dataset' : (validation.value === datasetToAdd.dataset_name ? validation.error : ''))}
        />
      )}
    />
    <Box display="flex" justifyContent="right" marginY={1}>
      {addDatasetButton}
    </Box>
    {apiError && <Typography color="error" role='dataset-api-error'>
      {apiError}
    </Typography>}
    <Divider/>
    {columns && values.length > 0 && <Datatable columns={columns} data={values.filter(value => !(value?.error_dataset)).map(value => Object({dataset: value, onRemove: onRemove}))}>
      <DatatableTable actions={DatasetsActions} noHeader />
    </Datatable>}
  </Box>
}
EditDatasets.propTypes = {
  values: PropTypes.arrayOf(PropTypes.object).isRequired,
  userDatasets: PropTypes.arrayOf(PropTypes.object).isRequired,
  onCreate: PropTypes.func.isRequired,
  onAdd: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired
}

const DatasetsActions = React.memo((props) => {
  const {data} = props
  const location = useLocation()

  const openDatasetUrl = useMemo(() => {
    const path = `datasets/dataset/id/${data.dataset.dataset_id}`
    return getUrl(path, location)
  }, [data, location])

  return <Box display={'inline-block'}>
    {data.dataset.dataset_id && <IconButton size='small' component={Link} to={openDatasetUrl} target="_blank">
      <Tooltip title="Open in new tab">
        <OpenInNewIcon />
      </Tooltip>
    </IconButton>}
    <IconButton size='small' onClick={() => data.onRemove(data.dataset)} disabled={!!data.dataset.doi && !data.dataset?.notSubmitted} style={{pointerEvents: 'auto'}} data-testid='dataset-delete-action'>
      <Tooltip title={(data.dataset.doi && !data.dataset?.notSubmitted ? 'The dataset cannot be removed. A DOI has been assigned to the dataset.' : 'Remove the dataset')}>
        <DeleteIcon />
      </Tooltip>
    </IconButton>
  </Box>
})
DatasetsActions.propTypes = {
  data: PropTypes.object.isRequired
}

const useEditMetaDataDialogStyles = makeStyles(theme => ({
  dialog: {
    width: '100%'
  }
}))

function EditMetaDataDialog({...props}) {
  const {isIcon, selectedEntries} = props
  const classes = useEditMetaDataDialogStyles()
  const {api, user} = useApi()
  const {raiseError} = useErrors()
  const {uploadId, upload, entries, updateUpload, requestRefreshUpload} = useUploadPageContext()
  const [open, setOpen] = useState(false)
  const [openConfirmDialog, setOpenConfirmDialog] = useState(false)
  const isProcessing = upload?.process_running
  const selectedEntriesIDs = selectedEntries['entry_id:any'] || selectedEntries['entry_id']
  const nSelected = (selectedEntries.upload_id ? upload?.entries : selectedEntriesIDs.length)
  const [userDatasets, setUserDatasets] = useState([])
  const [userDatasetsFetched, setUserDatasetsFetched] = useState(false)
  const [actions, setActions] = useState([])

  const handleDiscardChanges = useCallback(() => {
    setOpenConfirmDialog(false)
    setOpen(false)
  }, [])

  useEffect(() => {
    if (open && !userDatasetsFetched) {
      setUserDatasetsFetched(true)
      api.get(`/datasets?page_size=${1000}&page=${1}&user_id=${user.sub}`)
        .then(datasets => {
          setUserDatasets(datasets?.data)
        })
        .catch(err => {
          setUserDatasets([])
          raiseError(err)
        })
    }
  }, [api, user, raiseError, open, userDatasetsFetched, setUserDatasetsFetched])

  const selectedEntriesObjects = useMemo(() => {
    return selectedEntries.upload_id ? entries : entries.filter(entry => selectedEntriesIDs.includes(entry?.entry_id))
  }, [entries, selectedEntriesIDs, selectedEntries.upload_id])

  const defaultComment = useMemo(() => (
    entries?.length > 0 ? selectedEntriesObjects[0]?.entry_metadata?.comment || '' : ''
  ), [entries, selectedEntriesObjects])

  const defaultReferences = useMemo(() => {
    if (entries?.length < 0) {
      return []
    }
    const referencesList = selectedEntriesObjects.map(entry => entry?.entry_metadata?.references).flat()
    return referencesList.filter((value, index) => referencesList.indexOf(value) === index)
  }, [entries.length, selectedEntriesObjects])

  const defaultDatasets = useMemo(() => {
    if (entries?.length < 0) {
      return []
    }
    const datasetsList = selectedEntriesObjects.map(entry => entry?.entry_metadata?.datasets)
    return userDatasets.filter(datasetFullData => datasetsList.flat().map(dataset => dataset.dataset_id).includes(datasetFullData.dataset_id))
  }, [selectedEntriesObjects, entries.length, userDatasets])

  const handleOpenDialog = useCallback(() => {
    setActions([])
    setOpen(true)
  }, [])

  const createNewDatasets = useCallback(() => {
    const promises = actions.filter(action => action.create_dataset).map(action => api.post(`/datasets`, {dataset_name: action.create_dataset}))
    return Promise.all(promises)
  }, [api, actions])

  const edit = useCallback((metadata, verify_only) => {
    return new Promise((resolve, reject) => {
      try {
        const requestBody = {metadata: metadata, verify_only: verify_only, owner: 'user'}
        if (selectedEntriesIDs) requestBody.query = selectedEntries
        api.post(`uploads/${uploadId}/edit`, requestBody)
          .then(resolve)
          .catch(reject)
      } catch (error) {
        reject(error)
      }
    })
  }, [api, uploadId, selectedEntries, selectedEntriesIDs])

  const submitChanges = useCallback((metadata) => {
    edit(metadata, true)
      .then(response => {
        edit(metadata, false)
          .then(results => {
            setActions([])
            updateUpload({upload: results.data})
            setOpen(false)
          }).catch(err => {
            if (err.status === 504) {
              requestRefreshUpload()
            }
            raiseError(err)
          })
      })
      .catch(err => {
        const errors = err.apiMessage
        if (Array.isArray(errors)) {
          errors.forEach(error => {
            if (error.loc.includes('references')) setActions(oldActions => [...oldActions, {'error_reference': error.msg}])
            if (error.loc.includes('datasets')) setActions(oldActions => [...oldActions, {'error_dataset': error.msg}])
          })
        } else {
          raiseError(err)
        }
      })
  }, [edit, updateUpload, requestRefreshUpload, raiseError])

  const isCommentChanged = useMemo(() => !!actions.find(action => 'set_comment' in action), [actions])
  const isReferencesChanged = useMemo(() => !!actions.find(action => 'add_reference' in action || 'remove_reference' in action), [actions])
  const isDatasetChanged = useMemo(() => !!actions.find(action => 'add_dataset' in action || 'remove_dataset' in action || 'create_dataset' in action), [actions])

  const comment = useMemo(() => {
    const action = actions.find(action => 'set_comment' in action)
    return (action ? action.set_comment : defaultComment)
  }, [actions, defaultComment])

  const references = useMemo(() => {
    let references = [...defaultReferences]
    actions.forEach(action => {
      if (action.add_reference) references = [...references, action.add_reference]
      if (action.remove_reference) references = references.filter(reference => reference !== action.remove_reference)
      if (action.error_reference) references = [...references, action]
    })
    return references
  }, [actions, defaultReferences])

  const datasets = useMemo(() => {
    let datasets = [...defaultDatasets]
    actions.forEach(action => {
      if (action.add_dataset) {
        const notSubmittedDataset = userDatasets.find(datasetFullData => action.add_dataset === datasetFullData.dataset_id)
        notSubmittedDataset.notSubmitted = true
        datasets = [...datasets, notSubmittedDataset]
      }
      if (action.create_dataset) datasets = [...datasets, {'dataset_name': action.create_dataset}]
      if (action.remove_dataset) datasets = datasets.filter(dataset => dataset.dataset_id !== action.remove_dataset)
      if (action.error_dataset) datasets = [...datasets, action]
    })
    return datasets
  }, [actions, defaultDatasets, userDatasets])

  const handleSubmitChanges = useCallback(() => {
    if (isCommentChanged || isReferencesChanged || isDatasetChanged) {
      const metadata = {}
      if (isCommentChanged) metadata.comment = comment
      if (isReferencesChanged) {
        metadata.references = {}
        if (actions.find(action => 'add_reference' in action)) metadata.references.add = actions.flatMap(action => action.add_reference || [])
        if (actions.find(action => 'remove_reference' in action)) metadata.references.remove = actions.flatMap(action => action.remove_reference || [])
      }
      if (isDatasetChanged) {
        metadata.datasets = {}
        createNewDatasets().then(newDatasets => {
          const newDatasetsIDs = newDatasets.map(_dataset => _dataset.dataset_id)
          if (actions.find(action => 'add_dataset' in action || 'create_dataset' in action)) metadata.datasets.add = actions.flatMap(action => action.add_dataset || []).concat(newDatasetsIDs)
          if (actions.find(action => 'remove_dataset' in action)) metadata.datasets.remove = actions.flatMap(action => action.remove_dataset || [])
          submitChanges(metadata)
        })
      } else {
        submitChanges(metadata)
      }
    } else {
      setOpen(false)
    }
  }, [actions, comment, createNewDatasets, isCommentChanged, isDatasetChanged, isReferencesChanged, submitChanges])

  const handleSetComment = useCallback((value) => {
    const action = actions.find(action => 'set_comment' in action)
    if (action) {
      const newActions = [...actions]
      if (value === defaultComment) {
        newActions.splice(actions.indexOf(action), 1)
      } else {
        newActions[actions.indexOf(action)] = {set_comment: value}
      }
      setActions(newActions)
    } else {
      setActions(oldActions => [...oldActions, {set_comment: value}])
    }
  }, [actions, defaultComment])

  const handleAddReference = useCallback((value) => {
    setActions(oldActions => oldActions.filter(action => !('error_reference' in action)))
    if (actions.flatMap(action => action.remove_reference || []).includes(value)) {
      setActions(_actions => _actions.filter(action => action.remove_reference !== value))
    } else {
      setActions(_actions => [..._actions, {add_reference: value}])
    }
  }, [actions])

  const handleRemoveReference = useCallback((value) => {
    setActions(oldActions => oldActions.filter(action => !('error_reference' in action)))
    if (actions.flatMap(action => action.add_reference || []).includes(value)) {
      setActions(_actions => _actions.filter(action => action.add_reference !== value))
    } else {
      setActions(_actions => [..._actions, {remove_reference: value}])
    }
  }, [actions])

  const handleCreateDataset = useCallback((value) => {
    setActions(oldActions => oldActions.filter(action => !('error_dataset' in action)))
    if (value && !actions.find(action => action.create_dataset === value)) {
      setActions(_actions => [..._actions, {create_dataset: value}])
    }
  }, [actions])

  const handleAddDataset = useCallback((value) => {
    if (value) {
      setActions(oldActions => oldActions.filter(action => !('error_dataset' in action)))
      if (actions.flatMap(action => action.remove_dataset || []).includes(value.dataset_id)) {
        setActions(_actions => _actions.filter(action => action.remove_dataset !== value.dataset_id))
      } else {
        setActions(_actions => [..._actions, {add_dataset: value.dataset_id}])
      }
    }
  }, [actions])

  const handleRemoveDataset = useCallback((value) => {
    setActions(oldActions => oldActions.filter(action => !('error_dataset' in action)))
    if (actions.flatMap(action => action.create_dataset || []).includes(value.dataset_name)) {
      setActions(_actions => _actions.filter(action => action.create_dataset !== value.dataset_name))
    } else if (actions.flatMap(action => action.add_dataset || []).includes(value.dataset_id)) {
      setActions(_actions => _actions.filter(action => action.add_dataset !== value.dataset_id))
    } else {
      setActions(_actions => [..._actions, {remove_dataset: value.dataset_id}])
    }
  }, [actions])

  const handleConfirm = useCallback(() => {
    if (isCommentChanged || isReferencesChanged || isDatasetChanged) {
      setOpenConfirmDialog(true)
    } else {
      setOpen(false)
    }
  }, [isCommentChanged, isDatasetChanged, isReferencesChanged])

  return <React.Fragment>
    {isIcon && <IconButton onClick={() => handleOpenDialog()}>
      <Tooltip title="Edit metadata">
        <EditIcon />
      </Tooltip>
    </IconButton>}
    {!isIcon && <Button onClick={() => handleOpenDialog()} variant='contained' color='primary' disabled={isProcessing} data-testid='edit-metadata-button'>
      {upload?.entries && (upload?.entries > 1 ? `Edit metadata of all ${upload?.entries} entries` : `Edit metadata of all the entries`)}
    </Button>}
    {open && <Dialog classes={{paper: classes.dialog}} open={open} disableEscapeKeyDown data-testid='edit-metadata-dialog'>
      <DialogTitle>Edit upload metadata</DialogTitle>
      <DialogContent>
        <DialogContentText>
          You can add, edit, or remove the metadata for the selected entries.
          <br/>
          {nSelected} of {upload?.entries} {upload?.entries === 1 ? 'entry' : 'entries'} is selected.
        </DialogContentText>
        <Divider/>
        <div>
          <EditComments value={defaultComment} onChange={handleSetComment}/>
          <EditReferences values={references} onAdd={handleAddReference} onRemove={handleRemoveReference}/>
          <EditDatasets values={datasets} userDatasets={userDatasets} onCreate={handleCreateDataset} onAdd={handleAddDataset} onRemove={handleRemoveDataset}/>
        </div>
      </DialogContent>
      <DialogActions>
        <span style={{flexGrow: 1}} />
        <Button onClick={handleConfirm} color="secondary">
          Cancel
        </Button>
        <Button onClick={handleSubmitChanges} disabled={!(isCommentChanged || isReferencesChanged || isDatasetChanged)} color="secondary">
          Submit
        </Button>
      </DialogActions>
      <Dialog
        open={openConfirmDialog}
        aria-describedby="alert-dialog-description"
      >
        <DialogContent>
          <DialogContentText id="alert-dialog-description">
            Your changes are not submitted yet. Discard changes?
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenConfirmDialog(false)} autoFocus>Cancel</Button>
          <Button onClick={handleDiscardChanges}>Discard</Button>
        </DialogActions>
      </Dialog>
    </Dialog>}
  </React.Fragment>
}
EditMetaDataDialog.propTypes = {
  isIcon: PropTypes.bool,
  selectedEntries: PropTypes.object
}

export default EditMetaDataDialog
