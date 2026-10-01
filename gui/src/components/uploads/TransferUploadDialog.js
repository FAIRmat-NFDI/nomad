import PropTypes from 'prop-types'
import React, { useEffect, useMemo, useState } from 'react'
import { Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, TextField, Tooltip, LinearProgress } from "@material-ui/core"
import { docsBase, oasis } from '../../config'
import { Alert, AlertTitle } from '@material-ui/lab'
import { HelpButton } from '../Help'
import { EmbargoSelect } from './UploadOverview'
import { useApi, useLoading } from '../api'
import { useUploadPageContext } from './UploadPageContext'
import { useErrors } from '../errors'

function isValidHttpUrl(str) {
  try {
    const url = new URL(str)
    return url.protocol === "http:" || url.protocol === "https:"
  } catch (_) {
    return false
  }
}

function ConfirmDialogPublishUploadExternally({
  open, setOpen,
  embargo, authToken, targetDeploymentUrl
}) {
  const { api } = useApi()
  const { uploadId, updateUpload } = useUploadPageContext()
  const isLoading = useLoading()
  const { raiseError } = useErrors()

  const buttonLabel = targetDeploymentUrl ? 'Transfer to external OASIS' : 'transfer to Central NOMAD'
  const targetName = targetDeploymentUrl ? 'external deployment' : 'central NOMAD'

  const handlePublishExternally = ({ embargo_length, auth_token, target_deployment_url }) => {
    api.post(`/uploads/${uploadId}/action/transfer`, {
      embargo_length, auth_token, target_deployment_url
    })
      .then(results => {
        updateUpload({ upload: results.data })
      })
      .catch(raiseError)
  }
  const handlePublish = () => {
    setOpen(false)
    const body = {
      embargo_length: embargo,
      auth_token: authToken,
      target_deployment_url: targetDeploymentUrl
    }
    const cleaned = {}
    for (const [key, value] of Object.entries(body)) {
      if (value || value === 0) {
        cleaned[key] = value
      }
    }
    handlePublishExternally(cleaned)
  }

  return <>
    <Dialog
      open={open}
      onClose={() => setOpen(false)}
    >
      <DialogTitle>Confirm that you want to publish the upload to {targetName}</DialogTitle>
      <DialogContent>
        <DialogContentText>
          You are about to transfer this upload to <strong>{targetDeploymentUrl || 'central NOMAD'}</strong>.
        </DialogContentText>
        <DialogContentText>
          Please note that this upload will be published under the account that you used to generate the access token.
        </DialogContentText>
        <DialogContentText>
          The upload will be published to the {targetName} with the chosen <strong>embargo period</strong> ({embargo === 0 ? <strong>no embargo</strong> : <strong>{`${embargo} months`}</strong>}). Please check the {targetName} for the status of the upload.
          If this upload is already transferred to the target, this action has no effect.
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => setOpen(false)} disabled={isLoading}>Cancel</Button>
        <Button
          onClick={handlePublish}
          disabled={!authToken || isLoading}
        >
          {buttonLabel}
        </Button>
      </DialogActions>
    </Dialog>
  </>
}

ConfirmDialogPublishUploadExternally.propTypes = {
  open: PropTypes.bool,
  setOpen: PropTypes.func,
  targetDeploymentUrl: PropTypes.string,
  authToken: PropTypes.string,
  embargo: PropTypes.number
}

const TransferUploadDialog = ({ open, setOpen }) => {
  const { upload, isVisibleForAll } = useUploadPageContext()

  const [authToken, setAuthToken] = useState('')
  const [targetDeploymentUrl, setTargetDeploymentUrl] = useState('')
  const [touched, setTouched] = useState({
    targetDeploymentUrl: false
  })
  const [targetDeploymentUrlError, setTargetDeploymentUrlError] = useState(null)
  const [openConfirmDialog, setOpenConfirmDialog] = useState(false)
  const [embargo, setEmbargo] = useState(upload.embargo_length === undefined ? 0 : upload.embargo_length)
  const isLoading = useLoading()

  const formIsValid = useMemo(() => {
    if (targetDeploymentUrlError) return false
    if (!authToken) return false

    // target deployment is required on central nomad
    if (!oasis && !targetDeploymentUrl) return false

    return true
  }, [authToken, targetDeploymentUrl, targetDeploymentUrlError])

  const isPublished = upload.published
  const showFailedPublishExternally =
    upload?.current_process === "_publish_externally" &&
    upload?.process_status === "FAILURE" &&
    !isLoading
  const showSuccessPublishExternally =
    upload?.current_process === "_publish_externally" &&
    upload?.process_status === "SUCCESS" &&
    !isLoading

  const handleCancel = () => {
    setOpen(false)
  }
  const handleSubmit = () => {
    if (targetDeploymentUrl && !isValidHttpUrl(targetDeploymentUrl)) {
      return
    }
    setOpenConfirmDialog(true)
  }

  const cleanUpPublishErrorMessage = (error) => {
    let cleaned = error
    if (cleaned.includes("Upload is already published to the central NOMAD.")) {
      cleaned = cleaned.replace('the central NOMAD', 'the target deployment')
    }
    cleaned = cleaned.replace('AssertionError: ', '')
    return cleaned
  }
  useEffect(() => {
    if (!touched.targetDeploymentUrl) {
      return null
    }
    if (!targetDeploymentUrl) {
      if (oasis) {
        // Oasis does not require to target url
        setTargetDeploymentUrlError(null)
      } else {
        setTargetDeploymentUrlError('Target deployment is required')
      }
    } else if (!isValidHttpUrl(targetDeploymentUrl)) {
      setTargetDeploymentUrlError('Input should be a valid URL')
    } else if (targetDeploymentUrl.startsWith('Berarer')) {
      setTargetDeploymentUrlError('Remove the "Bearer" prefix')
    } else if (!targetDeploymentUrl.endsWith('/api')) {
      setTargetDeploymentUrlError('Input should end with "/api"')
    } else {
      setTargetDeploymentUrlError(null)
    }
  }, [targetDeploymentUrl, touched.targetDeploymentUrl])

  return (
    <form autoComplete='off'>
      <Dialog open={open && isPublished} disableEscapeKeyDown>
        <DialogTitle>Transfer Upload</DialogTitle>
        <DialogContent>
          <ConfirmDialogPublishUploadExternally
            upload={upload}
            isPublished={isPublished}
            isVisibleForAll={isVisibleForAll}
            embargo={embargo}
            authToken={authToken}
            open={openConfirmDialog}
            setOpen={setOpenConfirmDialog}
            targetDeploymentUrl={targetDeploymentUrl}
          />
          {isLoading && <LinearProgress />}
          <DialogContentText style={{ marginTop: isLoading ? 10 : 0 }}>
            {`This feature allows you to transfer a published upload to${oasis ? " either" : ""} an external OASIS deployment${oasis ? " or the central NOMAD platform" : ""}. The transferred upload will remain published in the target system under the embargo period you select.`}
          </DialogContentText>
          <DialogContentText>
            Once the transfer begins, the process may take some time to complete. You can safely close the page while the transfer is in progress.
          </DialogContentText>
          <DialogContentText style={{ marginBottom: 15 }}>
            The transfer requires that the target deployment is network-accessible from the source deployment. E.g. if the target deployment is protected by a VPN, the publish action may fail due to connectivity restrictions.
          </DialogContentText>
          <DialogContentText style={{ marginBottom: 5 }}>
            Target deployment API URL
          </DialogContentText>
          <TextField
            placeholder='https://nomad-lab.eu/prod/v1/api'
            value={targetDeploymentUrl}
            onBlur={() => setTouched(prev => ({...prev, targetDeploymentUrl: true}))}
            onChange={(e) => setTargetDeploymentUrl(e.target.value)}
            error={targetDeploymentUrlError}
            helperText={
              targetDeploymentUrl
                ? (
                  targetDeploymentUrlError || "Target: External Oasis"
                )
                : (
                  !oasis
                    ? "Target system URL required"
                    : "Default target: Central NOMAD"
                )
            }
            fullWidth
            disabled={isLoading}
            autoComplete={'off'}
          />
          <DialogContentText style={{ marginTop: 15, marginBottom: 5 }}>Access Token* <span>
            <Tooltip title="Access Token help">
              <HelpButton
                IconProps={{ fontSize: 'small' }}
                maxWidth="md"
                size="small"
                heading="Access Token Help"
                text={`
The access token is a temporary credential used to securely authorize the publish action to an external OASIS${oasis ? ' or the central NOMAD' : ''}. It is generated using the authenticated user's credentials, ensuring that the upload is performed on behalf of that specific user. Check the [documentation](${docsBase}/howto/manage/program/auth.html#keycloak-access-tokens) to know how to get an access token from the transfer target.
`}
              />
            </Tooltip>
          </span></DialogContentText>
          <TextField
            required
            value={authToken}
            onChange={(e) => setAuthToken(e.target.value)}
            disabled={!isPublished || isLoading}
            fullWidth
            placeholder='Token'
            type="password"
            inputMode="text"
            autoComplete="new-password"
            name="hiddenInput"
          />
          <DialogContentText style={{ marginTop: 15, marginBottom: 5 }}>Embargo period</DialogContentText>
          <EmbargoSelect
            embargo={embargo}
            onChange={setEmbargo}
            hideLabel
            disabledReason={
              isVisibleForAll
                ? 'Upload is publicly visible, embargo disabled'
                : !authToken
                  ? 'Provide an Access Token'
                  : isLoading
                    ? ' '
                    : null
            }
          />
          {
            showFailedPublishExternally && !isLoading && (
              <Alert severity='error' style={{ width: 'auto', marginTop: 20 }} >
                <AlertTitle>Last transfer failed</AlertTitle>
                {cleanUpPublishErrorMessage(
                  upload?.errors?.[0] || 'Unknown error'
                )}
              </Alert>
            )
          }
          {
            showSuccessPublishExternally && !isLoading && (
              <Alert severity='success' style={{ width: 'auto', marginTop: 20 }}>
                Last external publication succeded
              </Alert>
            )
          }
        </DialogContent>
        <DialogActions>
          <span style={{ flexGrow: 1 }} />
          <Button onClick={handleCancel} color="secondary" disabled={isLoading}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} color="secondary" disabled={!formIsValid || isLoading}>
            Submit
          </Button>
        </DialogActions>
      </Dialog>
    </form>
  )
}

TransferUploadDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  setOpen: PropTypes.func.isRequired
}

export default TransferUploadDialog
