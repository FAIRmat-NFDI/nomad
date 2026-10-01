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
  Accordion, AccordionDetails, AccordionSummary, Box, Button, Checkbox, Dialog,
  DialogContent, DialogTitle, FormControl, FormHelperText, Grid, IconButton,
  InputLabel, Link, makeStyles, MenuItem, Select, Step, StepContent, StepLabel, Stepper,
  Tooltip, Typography
} from '@material-ui/core'
import DialogActions from '@material-ui/core/DialogActions'
import DialogContentText from '@material-ui/core/DialogContentText'
import FormControlLabel from '@material-ui/core/FormControlLabel'
import ReprocessIcon from '@material-ui/icons/Autorenew'
import DownloadIcon from '@material-ui/icons/CloudDownload'
import StopIcon from '@material-ui/icons/Stop'
import UploadIcon from '@material-ui/icons/CloudUpload'
import ExpandMoreIcon from '@material-ui/icons/ExpandMore'
import MembersIcon from '@material-ui/icons/Group'
import ReloadIcon from '@material-ui/icons/Replay'
import SendIcon from '@material-ui/icons/Send'
import PropTypes from 'prop-types'
import React, { useCallback, useEffect, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { docsBase } from '../../config'
import { formatTimestamp } from '../../utils'
import { CodeList } from '../About'
import { useDataStore } from "../DataStore"
import Markdown from '../Markdown'
import Page from '../Page'
import { useApi } from '../api'
import { SourceApiCall, SourceApiDialogButton } from '../buttons/SourceDialogButton'
import { combinePagination } from '../datatable/Datatable'
import Download from '../entry/Download'
import { useErrors } from '../errors'
import DialogLink from '../utils/DialogLink'
import WithButton from '../utils/WithButton'
import CreateEntry from './CreateEntry'
import DeleteUploadsButton from './DeleteUploadsButton'
import EditMembersDialog from './EditMembersDialog'
import EditMetaDataDialog from './EditMetaDataDialog'
import FilesBrower from './FilesBrowser'
import ProcessingTable from './ProcessingTable'
import UploadName from './UploadName'
import { useUploadPageContext } from './UploadPageContext'
import UploadProgressDialog from './UploadProgressDialog'
import UploadSearchMenu from './UploadSearchMenu'
import UploadStatusIcon from './UploadStatusIcon'
import TransferUploadDialog from './TransferUploadDialog'
import Ellipsis from '../visualization/Ellipsis'

const useDropButtonStyles = makeStyles(theme => ({
  dropzone: {
    width: '100%'
  },
  dropzoneAccept: {
    '& button': {
      background: theme.palette.secondary.main,
      color: theme.palette.common.white
    }
  },
  dropzoneReject: {
    '& button': {
      background: theme.palette.error.main,
      color: theme.palette.common.white
    }
  }
}))

function DropButton({ onDrop, ...buttonProps }) {
  const classes = useDropButtonStyles()
  const { getRootProps, getInputProps, isDragAccept, isDragReject } = useDropzone({ onDrop })
  const className = (isDragAccept && classes.dropzoneAccept) || (isDragReject && classes.dropzoneReject) || classes.dropzone
  return (
    <div className={className}>
      <div {...getRootProps()}>
        <input {...getInputProps()} />
        <Button
          variant="contained"
          color="primary"
          startIcon={<UploadIcon />}
          {...buttonProps}
        >
          Drop files here or click to open dialog
        </Button>
      </div>
    </div>
  )
}
DropButton.propTypes = {
  onDrop: PropTypes.func
}

function EmbargoSelect({ embargo, onChange, disabledReason, variant, hideLabel = false }) {
  const classes = useStyles()
  return <FormControl style={{ width: '100%' }} variant={variant} className={classes.formControl}>
    {
      !hideLabel && (
        <InputLabel shrink htmlFor="embargo-label-placeholder">
          Embargo period
        </InputLabel>
      )
    }
    <Select
      value={embargo}
      onChange={event => onChange(event.target.value)}
      labelId='embargo-label-placeholder'
      displayEmpty
      name="embargo"
      disabled={!!disabledReason}
    >
      <MenuItem value={0}>
        <em>No embargo</em>
      </MenuItem>
      <MenuItem value={3}>3</MenuItem>
      <MenuItem value={6}>6</MenuItem>
      <MenuItem value={12}>12</MenuItem>
      <MenuItem value={24}>24</MenuItem>
      <MenuItem value={36}>36</MenuItem>
    </Select>
    <FormHelperText>
      {disabledReason ||
        (embargo > 0 ? 'months before the data becomes public' : 'publish without embargo')}
    </FormHelperText>
  </FormControl>
}

EmbargoSelect.propTypes = {
  embargo: PropTypes.number,
  onChange: PropTypes.func,
  disabledReason: PropTypes.string,
  variant: PropTypes.string,
  hideLabel: PropTypes.bool
}

function PublishUpload({ upload, onPublish, isVisibleForAll }) {
  const [embargo, setEmbargo] = useState(upload.embargo_length === undefined ? 0 : upload.embargo_length)
  const [openConfirmDialog, setOpenConfirmDialog] = useState(false)
  const handlePublish = () => {
    setOpenConfirmDialog(false)
    onPublish({ embargo_length: embargo })
  }

  useEffect(() => {
    if (isVisibleForAll) {
      setEmbargo(0)
    }
  }, [isVisibleForAll])

  if (upload.published) {
    return <Markdown>{`
      This upload has already been published.
    `}</Markdown>
  }

  const buttonLabel = embargo > 0 ? 'Publish with embargo' : 'Publish'

  return <React.Fragment>
    <Dialog
      open={openConfirmDialog}
      onClose={() => setOpenConfirmDialog(false)}
    >
      <DialogTitle>Confirm that you want to publish the upload</DialogTitle>
      <DialogContent>
        <DialogContentText>
          You are about the publish this upload. The upload cannot be removed and
          the files and entries in this upload cannot be changed after publication.
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => setOpenConfirmDialog(false)} autoFocus>Cancel</Button>
        <Button onClick={handlePublish}>{buttonLabel}</Button>
      </DialogActions>
    </Dialog>
    <Markdown>{`
      If you agree this upload will be published and move out of your private staging
      area into the public NOMAD. This step is final. All public data will be made available under the Creative
      Commons Attribution license ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).

      If you wish, you can put an embargo on your data. This makes some metadata (e.g.
      chemical formula, system type, spacegroup, etc.) public, but the raw-file
      and archive contents remain hidden (except to you, and users you explicitly
      share the data with).
      You can already create datasets and assign DOIs for data with embargo, e.g.
      to put it into your unpublished paper.
      The embargo will last up to 36 month. Afterwards, your data will be made publicly
      available. You can also lift the embargo sooner if you wish.
    `}</Markdown>
    <Box marginTop={2}>
      <Grid container direction="row" spacing={2}>
        <Grid item style={{ width: 300 }}>
          <EmbargoSelect
            embargo={embargo}
            onChange={setEmbargo}
            disabledReason={isVisibleForAll ? 'Upload is publicly visible, embargo disabled' : null}
          />
        </Grid>
        <Grid item>
          <Box marginTop={2} >
            <Button
              style={{ height: 32, minWith: 100 }}
              size="small" variant="contained"
              onClick={() => setOpenConfirmDialog(true)} color="primary"
              disabled={upload.process_running}
              data-testid='publish-upload-button'
            >
              {buttonLabel}
            </Button>
          </Box>
        </Grid>
      </Grid>
    </Box>
  </React.Fragment>
}
PublishUpload.propTypes = {
  upload: PropTypes.object,
  onPublish: PropTypes.func,
  isVisibleForAll: PropTypes.bool
}

function ProcessingStatus({ data }) {
  const { pagination, upload, processing_successful, processing_failed } = data
  let mainMessage = null
  if (upload.process_running) {
    mainMessage = 'Processing ...'
  } else {
    if (upload.process_status === 'SUCCESS') {
      mainMessage = 'Processing completed'
    } else if (upload.process_status === 'FAILURE') {
      mainMessage = 'Processing failed ' + upload.errors.join(', ')
    } else {
      mainMessage = 'Waiting for processing ...'
    }
  }

  return (
    <Box marginTop={1} marginBottom={2}>
      <Typography
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem'
        }}
      >
        {/* left: ellipsized main message */}
        <Ellipsis tooltip={mainMessage} style={{ flex: 1, minWidth: 0 }}>
          {mainMessage}
        </Ellipsis>

        {/* right: always visible status */}
        <span style={{ flexShrink: 0 }}>
          {`${processing_successful}/${pagination?.total} entries processed`}
          {processing_failed > 0 && `, ${processing_failed} failed`}
        </span>
      </Typography>
    </Box>
  )
}
ProcessingStatus.propTypes = {
  data: PropTypes.object
}

const useStyles = makeStyles(theme => ({
  stepper: {
    backgroundColor: 'inherit',
    paddingLeft: 0,
    paddingRight: 0
  },
  stepContent: {
    marginBottom: theme.spacing(2)
  },
  mixedButtonIcon: {
    fontSize: '150%',
    marginRight: theme.spacing(1)
  }
}))

export function SupportedCodes({ children }) {
  return <DialogLink title="Supported codes" text={children}>
    <Typography>
      For the following codes, we support automatic parsing of data into
      entries: <CodeList withUploadInstructions />. Click the code to get more
      specific information about how to prepare your files.
    </Typography>
  </DialogLink>
}
SupportedCodes.propTypes = {
  children: PropTypes.node
}

export function UploadDocumentation({ children }) {
  return <Link href={`${docsBase}/howto/manage/gui/upload.html`}>
    {children}
  </Link>
}
UploadDocumentation.propTypes = {
  children: PropTypes.node
}

export function SchemaDocumentation({ children }) {
  return <Link href={`${docsBase}/reference/glossary.html#schema`}>
    {children}
  </Link>
}
SchemaDocumentation.propTypes = {
  children: PropTypes.node
}

function UploadOverview(props) {
  const classes = useStyles()
  const dataStore = useDataStore()
  const { api, user } = useApi()
  const { raiseError } = useErrors()
  const {
    uploadId, upload, entries, apiData, hasUpload, isProcessing, error, isVisibleForAll,
    isWriter, pagination, deleteRequested, updateUpload, requestRefreshUpload,
    isMainAuthor } = useUploadPageContext()
  const [uploading, setUploading] = useState(null)
  const [openEmbargoConfirmDialog, setOpenEmbargoConfirmDialog] = useState(false)
  const [openTransferDialog, setOpenTransferDialog] = useState(false)
  const [readme, setReadme] = useState(null)
  const [openEditMembersDialog, setOpenEditMembersDialog] = useState(false)

  const handleIsVisibleForAll = useCallback((event) => {
    const action = event.target.checked ? 'add' : 'remove'
    api.post(`/uploads/${uploadId}/edit`, {
      metadata: {
        'reviewer_groups': { [action]: 'all' }
      }
    })
      .then(results => {
        updateUpload({ upload: results.data })
      })
      .catch(error => {
        if (error.status === 504) {
          requestRefreshUpload()
        }
        raiseError(error)
      })
  }, [api, updateUpload, requestRefreshUpload, raiseError, uploadId])

  useEffect(() => {
    if (uploading) return
    dataStore.breadcrumb.setUpload(upload?.upload_name || 'Upload')
    api.get(`/uploads/${uploadId}/raw/README.md`)
      .then(setReadme)
      .catch(error => {
        setReadme(null)
        if (error.name !== 'DoesNotExist') {
          raiseError(error)
        }
      })
  }, [api, raiseError, uploadId, uploading, setReadme, dataStore.breadcrumb, upload?.upload_name])

  const handleDropFiles = useCallback(files => {
    if (!files[0]?.name) {
      return // Not dropping a file, but something else. Ignore.
    }
    const formData = new FormData() // eslint-disable-line no-undef
    for (const file of files) {
      formData.append('file', file)
    }
    setUploading(0)
    api.put(`/uploads/${uploadId}/raw/`, formData, {
      onUploadProgress: (progressEvent) => {
        const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total)
        setUploading(percentCompleted)
      }
    })
      .then(results => updateUpload({ upload: results.data }))
      .catch(raiseError)
      .finally(() => {
        setUploading(null)
      })
  }, [uploadId, updateUpload, setUploading, api, raiseError])

  const handleNameChange = (upload_name) => {
    api.post(`/uploads/${uploadId}/edit`, { metadata: { upload_name: upload_name } })
      .then(results => {
        updateUpload({ upload: results.data })
      })
      .catch(error => {
        if (error.status === 504) {
          requestRefreshUpload()
        }
        raiseError(error)
      })
  }

  const handlePublish = ({ embargo_length }) => {
    api.post(`/uploads/${uploadId}/action/publish?embargo_length=${embargo_length}`)
      .then(results => {
        updateUpload({ upload: results.data })
      })
      .catch(error => {
        if (error.status === 504) {
          requestRefreshUpload()
        }
        raiseError(error)
      })
  }

  const handleLiftEmbargo = () => {
    setOpenEmbargoConfirmDialog(false)
    api.post(`/uploads/${uploadId}/edit`, { metadata: { embargo_length: 0 } })
      .then(results => {
        updateUpload({ upload: results.data })
      })
      .catch(error => {
        if (error.status === 504) {
          requestRefreshUpload()
        }
        raiseError(error)
      })
  }

  const handleReload = () => {
    requestRefreshUpload()
  }

  const handleReprocess = () => {
    api.post(`/uploads/${uploadId}/action/process`)
      .then(results => updateUpload({ upload: results.data }))
      .catch(raiseError)
  }

  const handleStopProcessing = () => {
    api.post(`/uploads/${uploadId}/action/stop-processing`)
      .then(results => updateUpload({upload: results.data}))
      .catch(raiseError)
  }

  const handleDelete = () => {
    api.delete(`/uploads/${uploadId}`)
      .then(results => {
        updateUpload({ upload: results.data })
      })
      .catch(error => {
        if (error.status === 504) {
          requestRefreshUpload()
        }
        raiseError(error)
      })
  }

  if (!hasUpload || !entries) {
    return <Page limitedWidth>
      {(error ? <Typography>{error.apiMessage || error.message || 'Failed to load'}</Typography> : <Typography>loading ...</Typography>)}
    </Page>
  }

  const isAuthenticated = api.keycloak.authenticated
  const isPublished = upload.published
  const isEmpty = upload.entries === 0

  return (
    <Page limitedWidth>
      <UploadProgressDialog uploading={uploading} />
      <Grid container spacing={2} alignItems="center">
        <Grid item>
          <UploadStatusIcon data={upload} user={user} fontSize="large" />
        </Grid>
        <Grid item style={{ flexGrow: 1 }}>
          <UploadName upload_name={upload?.upload_name} onChange={handleNameChange} />
          <WithButton clipboard={uploadId}>
            <Typography>upload id: {uploadId}</Typography>
          </WithButton>
        </Grid>
        <Grid item>
          <Box display={'flex'}>
            <UploadSearchMenu uploadId={uploadId}/>
            <Tooltip title="Edit upload members">
              <span>
                <IconButton
                  onClick={() => setOpenEditMembersDialog(true)}
                  disabled={!isWriter || isProcessing}
                  data-testid='edit-members-action'
                >
                  <MembersIcon/>
                </IconButton>
              </span>
              </Tooltip>
            <EditMembersDialog open={openEditMembersDialog} setOpen={setOpenEditMembersDialog} />
            <Download
              component={IconButton} tooltip="Download files"
              url={`uploads/${uploadId}/raw/?compress=true`}
              data-testid='upload-download-action'
            >
              <DownloadIcon />
            </Download>
            <Tooltip title="Reload">
              <span>
                <IconButton onClick={handleReload}>
                  <ReloadIcon />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Reprocess">
              <span>
                <IconButton disabled={isPublished || !isWriter} onClick={handleReprocess} data-testid='upload-reprocess-action'>
                  <ReprocessIcon />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Stop processing">
              <span>
                <IconButton disabled={isPublished || !isWriter || upload.process_status !== 'PENDING'}
                            onClick={handleStopProcessing} data-testid='upload-stop-processing-action'>
                  <StopIcon/>
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title={isPublished ? "Transfer upload" : "The upload must be published before it can be transferred"}>
              <span>
                <IconButton disabled={!isPublished} onClick={() => setOpenTransferDialog(true)}>
                  <SendIcon/>
                </IconButton>
              </span>
            </Tooltip>
            <TransferUploadDialog open={openTransferDialog} setOpen={setOpenTransferDialog}/>
            <SourceApiDialogButton maxWidth="lg" fullWidth>
              <SourceApiCall {...apiData} />
            </SourceApiDialogButton>
            <DeleteUploadsButton
              tooltip="Delete upload"
              disabled={isPublished || !isMainAuthor}
              data-testid='upload-delete-action'
              uploads={[upload]}
              onConfirm={handleDelete}
            />
          </Box>
        </Grid>
      </Grid>
      {readme && (
        <Box marginLeft={4} marginTop={2} marginBottom={0} marginRight={1}>
          <Accordion defaultExpanded>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              README.md
            </AccordionSummary>
            <AccordionDetails>
              <Markdown>{readme}</Markdown>
            </AccordionDetails>
          </Accordion>
        </Box>
      )}
      <Stepper classes={{ root: classes.stepper }} orientation="vertical" >
        <Step expanded active={false}>
          <StepLabel>Prepare and upload your files</StepLabel>
          <StepContent>
            {isPublished && <Typography className={classes.stepContent}>
              This upload is published and it&apos;s files can&apos;t be modified anymore.
            </Typography>}
            {!isPublished && isAuthenticated && isWriter && (
              <React.Fragment>
                <Typography className={classes.stepContent}>
                  Here you can upload files. Top-level .zip/.tar files will be uncompressed automatically. For more information,
                  see our documentation on <UploadDocumentation>uploading
                    files</UploadDocumentation> or view the <SupportedCodes>supported codes</SupportedCodes>.
                  Optionally, you can also create an entry from built-in or
                  uploaded schemas. Please take a look at our documentation on <SchemaDocumentation>schemas</SchemaDocumentation>.
                </Typography>
                <Box display="flex" flexDirection="row">
                  <Box flexGrow={1}>
                    <DropButton
                      className={classes.stepContent}
                      size="large"
                      fullWidth onDrop={handleDropFiles}
                      disabled={isProcessing}
                    />
                  </Box>
                  <Box marginLeft={2}>
                    <CreateEntry
                      size="large"
                      disabled={isProcessing}
                      variant="contained"
                      color="default"
                    >
                      Create from schema
                    </CreateEntry>
                  </Box>
                </Box>
              </React.Fragment>
            )}
            <div className={classes.stepContent}>
              <FilesBrower uploadId={uploadId} disabled={isProcessing || deleteRequested} />
            </div>
          </StepContent>
        </Step>
        <Step expanded={!isEmpty} active={false}>
          <StepLabel>Process data</StepLabel>
          <StepContent>
            <ProcessingStatus data={apiData.response} />
            <ProcessingTable
              data={entries.map(entry => ({ ...entry.entry_metadata, ...entry }))}
              pagination={combinePagination(pagination, apiData.response?.pagination)}
              customTitle='entry'
              onPaginationChanged={newPagination => updateUpload({ pagination: newPagination })} />
          </StepContent>
        </Step>
        {(isAuthenticated && isWriter) && <Step expanded active={false}>
          <StepLabel>Edit visibility and access</StepLabel>
          <StepContent>
            <FormControlLabel
              data-testid='upload-visible-for-all-label'
              label="Enabling this will allow all users, including guests without an account, to view the upload even before it is published."
              control={
                <Checkbox
                  checked={isVisibleForAll}
                  disabled={upload.with_embargo || isProcessing}
                  onChange={handleIsVisibleForAll}
                />
              }
            />
            <Typography className={classes.stepContent}>
              You can edit the access to the upload by adding or removing users as upload members.
            </Typography>
            <Button
              onClick={() => setOpenEditMembersDialog(true)}
              variant='contained'
              color='primary'
              disabled={isProcessing}
            >
              <MembersIcon className={classes.mixedButtonIcon} /> Edit upload members
            </Button>
          </StepContent>
        </Step>}
        {(isAuthenticated && isWriter) && <Step expanded={!isEmpty} active={false}>
          <StepLabel>Edit metadata</StepLabel>
          <StepContent>
            <Typography className={classes.stepContent}>
              You can add more information about your data, like <i>comments</i>,
              <i>references</i> (e.g. links to publications). You can also create
              <i>datasets</i> from your entries.
            </Typography>
            <Typography className={classes.stepContent}>
              You can either select and edit individual entries from the list above, or
              edit all entries at once.
            </Typography>
            {!isEmpty && <EditMetaDataDialog selectedEntries={{ 'upload_id': upload.upload_id }} />}
          </StepContent>
        </Step>}
        {(isAuthenticated && isWriter) && <Step expanded={!isEmpty} active={false}>
          <StepLabel>Publish</StepLabel>
          <StepContent>
            {isPublished && <Typography className={classes.stepContent}>
              {upload?.with_embargo ? `This upload has been published under embargo with a period of ${upload?.embargo_length} months from ${formatTimestamp(upload?.publish_time)}.`
                : `This upload has already been published.`}
            </Typography>}
            {!isPublished && <PublishUpload
              upload={upload} onPublish={handlePublish} isVisibleForAll={isVisibleForAll}
            />}
            {isPublished && upload?.with_embargo && upload?.embargo_length > 0 &&
              <Button onClick={() => setOpenEmbargoConfirmDialog(true)} variant='contained' color='primary' disabled={isProcessing}>
                Lift Embargo
              </Button>}
            <Dialog
              open={openEmbargoConfirmDialog}
              aria-describedby="alert-dialog-description"
              onClose={() => setOpenEmbargoConfirmDialog(false)}
            >
              <DialogContent>
                <DialogContentText id="alert-dialog-description">
                  You are about lifting the embargo. The data will be publicly accessible.
                </DialogContentText>
              </DialogContent>
              <DialogActions>
                <Button onClick={() => setOpenEmbargoConfirmDialog(false)} autoFocus>Cancel</Button>
                <Button onClick={handleLiftEmbargo}>Lift Embargo</Button>
              </DialogActions>
            </Dialog>
          </StepContent>
        </Step>}
      </Stepper>
    </Page>
  )
}

export default UploadOverview
export {EmbargoSelect}
