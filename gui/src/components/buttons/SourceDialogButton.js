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
import React, { useMemo, useState, useContext } from 'react'
import PropTypes from 'prop-types'
import { Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Divider, IconButton, makeStyles, Tooltip, Typography } from '@material-ui/core'
import CodeIcon from '@material-ui/icons/Code'
import ReactJson from 'react-json-view'
import { CopyToClipboard } from 'react-copy-to-clipboard'
import ClipboardIcon from '@material-ui/icons/Assignment'
import Markdown from '../Markdown'
import { apiBase, docsBase } from '../../config'

export const ApiDataContext = React.createContext()

const useCodeStyles = makeStyles(theme => ({
  codeContainer: {
    display: 'flex',
    flexDirection: 'row',
    alignItems: 'flex-start'
  },
  code: {
    flexGrow: 1,
    overflow: 'auto',
    padding: '12px 18px',
    borderRadius: 4,
    backgroundColor: theme.palette.primary.veryLight,
    fontSize: 16,
    fontFamily: 'monospace',
    whiteSpace: 'pre'
  },
  codeActions: {
    marginTop: theme.spacing(3)
  }
}))
export const Code = React.memo(function Code({code}) {
  const classes = useCodeStyles()
  return <React.Fragment>
    <div className={classes.codeContainer}>
      <div className={classes.code}>
        {code}
      </div>
      <div className={classes.codeActions}>
        <CopyToClipboard text={code} onCopy={() => null}>
          <Tooltip title="Copy to clipboard">
            <IconButton>
              <ClipboardIcon />
            </IconButton>
          </Tooltip>
        </CopyToClipboard>
      </div>
    </div>
  </React.Fragment>
})
Code.propTypes = {
  code: PropTypes.string
}

/**
 * Button that can display content outside the button DOM. This is important to
 * not trigger any button hover/click events on the child component.
 */
export const ContentButton = React.memo(function ContentButton(props) {
  const {tooltip, children, buttonContent, ButtonComponent, ButtonProps} = props

  const button = useMemo(() => {
    return React.createElement(
      ButtonComponent,
      {...ButtonProps},
      buttonContent
    )
  }, [ButtonComponent, buttonContent, ButtonProps])
  return <React.Fragment>
    {children}
    {tooltip
      ? <Tooltip title={tooltip}>{button}</Tooltip>
      : button
    }
  </React.Fragment>
})
ContentButton.propTypes = {
  tooltip: PropTypes.string,
  buttonContent: PropTypes.node,
  ButtonComponent: PropTypes.elementType,
  ButtonProps: PropTypes.object,
  children: PropTypes.node
}

ContentButton.defaultProps = {
  ButtonComponent: IconButton
}

/**
 * Button that displays a simple dialog.
 */
export const DialogButton = React.memo(function DialogButton(props) {
  const {tooltip, buttonComponent, icon, label, title, children, buttonProps, ...DialogProps} = props
  const [open, setOpen] = useState(false)
  const handleClose = (value) => {
    setOpen(false)
  }

  return <ContentButton
    tooltip={tooltip}
    ButtonComponent={buttonComponent || (label ? Button : IconButton)}
    ButtonProps={{...buttonProps, onClick: () => setOpen(true)}}
    buttonContent={label || icon || <CodeIcon/>}
  >
    <Dialog {...DialogProps} open={open} onClose={handleClose}>
      {title && <DialogTitle>{title}</DialogTitle>}
      <DialogContent>
        {children}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  </ContentButton>
})
DialogButton.propTypes = {
  tooltip: PropTypes.string,
  icon: PropTypes.node,
  title: PropTypes.string,
  label: PropTypes.string,
  buttonComponent: PropTypes.elementType,
  buttonProps: PropTypes.object,
  children: PropTypes.node
}

export const SourceApiDialogButton = React.memo(function SourceApiDialogButton({description, children, buttonProps, ...props}) {
  const help = `The information on this page was loaded from the NOMAD API. You can also use
    the API to retrieve this information. Visit also our [API documentation](${docsBase}/howto/manage/program/api.html)
    or [API dashboard](${apiBase}).`

  return <DialogButton title="API" tooltip="API" buttonProps={buttonProps} {...props}>
    <Markdown text={description || help} />
    <SourceDialogDivider />
    {children}
  </DialogButton>
})
SourceApiDialogButton.propTypes = {
  description: PropTypes.string,
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ]),
  buttonProps: PropTypes.object
}

const CopyToClipboardButton = React.memo(function CopyToClipboardButton({code}) {
  return <CopyToClipboard text={code || ''} onCopy={() => null}>
    <Tooltip title="Copy to clipboard">
      <IconButton>
        <ClipboardIcon />
      </IconButton>
    </Tooltip>
  </CopyToClipboard>
})
CopyToClipboardButton.propTypes = {
  code: PropTypes.string
}

const useSourceApiCallStyles = makeStyles(theme => ({
  root: {},
  request: {
    width: '100%',
    display: 'flex',
    flexDirection: 'row',
    fontSize: 16,
    fontFamily: 'monospace',
    alignItems: 'center'
  },
  method: {
    minWidth: 60,
    fontWeight: 'bold'
  },
  code: {
    flexGrow: 1,
    padding: '12px 18px',
    overflow: 'auto',
    borderRadius: 4,
    backgroundColor: theme.palette.primary.veryLight,
    whiteSpace: 'pre'
  },
  heading: {
    marginTop: theme.spacing(2),
    marginBottom: theme.spacing(1),
    display: 'block'
  }
}))
export const SourceApiCall = React.memo(function ApiCall(props) {
  const apiData = useContext(ApiDataContext)
  const {method, url, description, body, response} = {...apiData, ...props}
  const classes = useSourceApiCallStyles()
  let formatedBody
  if (body) {
    // serialized json has "private" values that might contain circles removed
    formatedBody = JSON.stringify(
      body,
      (name, value) => name.startsWith('_') ? undefined : value,
      2
    )
  }

  return <div className={classes.root}>
    <Markdown text={description} />
    <Typography className={classes.heading} variant="body1"><b>Request</b></Typography>
    <div className={classes.request}>
      <span className={classes.method}>{method}</span>
      <span className={classes.code}>{url}</span>
      <CopyToClipboardButton code={url} />
    </div>
    {formatedBody && <div className={classes.request} style={{alignItems: 'start'}}>
      <span className={classes.method}>&nbsp;</span>
      <span className={classes.code}>{formatedBody}</span>
      <CopyToClipboardButton code={formatedBody} />
    </div>}
    <Typography className={classes.heading} variant="body1"><b>Response</b></Typography>
    <SourceJson data={response} />
  </div>
})
SourceApiCall.propTypes = {
  description: PropTypes.string,
  method: PropTypes.string,
  url: PropTypes.string,
  body: PropTypes.any,
  response: PropTypes.any
}

export const SourceJsonDialogButton = React.memo(function SourceJsonDialogButton({description, data, ...props}) {
  return <DialogButton title="Json" tooltip="Json data" {...props}>
    {description && <React.Fragment>
      <Markdown text={description} />
      <SourceDialogDivider />
    </React.Fragment>}
    <SourceJson data={data} />
  </DialogButton>
})
SourceJsonDialogButton.propTypes = {
  description: PropTypes.string,
  data: PropTypes.any
}

const useSourceJsonStyles = makeStyles(theme => ({
  stickyContainer: {
    zIndex: 1,
    width: '100%',
    height: 0,
    position: 'sticky',
    left: 0
  },
  actionContainer: {
    position: 'relative'
  },
  action: {
    position: 'absolute',
    right: 0,
    top: "1rem",
    transform: 'translate(0, -50%)'
  }
}))
export const SourceJson = React.memo(({data, ...props}) => {
  const styles = useSourceJsonStyles()
  const dataToRender = typeof data === 'object' ? data : {data: data}
  // serialized json has "private" values that might contain circles removed
  // might still have circles and fail
  let code = null
  try {
    code = JSON.stringify(data, (name, value) => name.startsWith('_') ? undefined : value, 2)
  } catch {}

  return <>
    {code && <div className={styles.stickyContainer}>
      <div className={styles.actionContainer}>
        <div className={styles.action}>
          <CopyToClipboardButton code={code} />
        </div>
      </div>
    </div>}
    <Box minHeight="2rem" display="flex" alignItems="center">
      <ReactJson
        src={dataToRender}
        enableClipboard={false}
        collapsed={2}
        displayObjectSize={false}
        {...props}
      />
    </Box>
  </>
})
SourceJson.propTypes = {
  data: PropTypes.any
}

export const SourceJsonCode = React.memo(function SourceJsonCode({data}) {
  // serialized json has "private" values that might contain circles removed
  const code = JSON.stringify(data, (name, value) => name.startsWith('_') ? undefined : value, 2)
  return <Code code={code} />
})
SourceJsonCode.propTypes = {
  data: PropTypes.object
}

export const SourceDialogDivider = (props) => <Box marginY={2}><Divider/></Box>
