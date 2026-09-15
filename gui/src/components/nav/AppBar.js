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

import React, { useMemo } from 'react'
import PropTypes from 'prop-types'
import {
  AppBar as MuiAppBar,
  Toolbar,
  Link,
  Button,
  Tooltip,
  LinearProgress,
  makeStyles
} from '@material-ui/core'
import LoginLogout from '../LoginLogout'
import UnitMenu from '../units/UnitMenu'
import MainMenu from './MainMenu'
import { useInfo, useLoading } from '../api'
import { appBase, guiBase, oasis, urlAbs } from '../../config'
import Breadcrumbs from './Breadcrumbs'

const newGuiPluginPackage = 'nomad_gui'

export const appBarHeight = 10

/**
 * Linear indefinite loading indicator that is connceted to API traffic.
 */
function LoadingIndicator({className}) {
  const loading = useLoading()
  return loading && <LinearProgress className={className} color="primary" />
}

LoadingIndicator.propTypes = {
  className: PropTypes.string
}

function useNewGuiUrl() {
  const info = useInfo()

  return useMemo(() => {
    const isInstalled = info?.plugin_packages
      ?.some(pluginPackage => pluginPackage.name === newGuiPluginPackage)
    if (!isInstalled) return undefined

    const prefix = info?.plugin_entry_points
      ?.find(entryPoint => entryPoint.plugin_package === newGuiPluginPackage && entryPoint.prefix)
      ?.prefix
    if (!prefix) return undefined

    const path = `${prefix}/select-gui`
    const base = appBase.endsWith('/') ? appBase : `${appBase}/`

    return urlAbs(path, base)
  }, [info])
}

const useStyles = makeStyles(theme => ({
  root: {
    zIndex: theme.zIndex.drawer + 1,
    backgroundColor: 'white',
    display: 'flex',
    flexDirection: 'column',
    padding: theme.spacing(1),
    height: theme.spacing(appBarHeight)
  },
  logo: {
    display: 'flex',
    alignItems: 'center',
    alignContent: 'flex-start',
    color: theme.palette.primary.main
  },
  toolbar: {
    display: 'flex',
    flexDirection: 'row'
  },
  logoImg: {
    height: 44,
    marginLeft: theme.spacing(1),
    marginRight: theme.spacing(2),
    marginTop: theme.spacing(1)
  },
  actions: {
    display: 'flex',
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'space-evenly'
  },
  menuItem: {
    borderColor: theme.palette.getContrastText(theme.palette.primary.main),
    marginRight: 0,
    minWidth: '6rem',
    justifyContent: 'flex-start'
  },
  navigation: {
    flexGrow: 1,
    marginRight: theme.spacing(1),
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    justifyContent: 'space-evenly'
  },
  progress: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0
  },
  crumbs: {
    padding: '9px 0px 9px 5px'
  },
  newGuiButton: {
    alignSelf: 'center',
    marginRight: theme.spacing(2),
    whiteSpace: 'nowrap',
    width: 150
  }
}))

/**
 * The App bar that is always shown at the top of the screen.
 */
export default function AppBar() {
  const styles = useStyles()
  const newGuiUrl = useNewGuiUrl()

  return <MuiAppBar position="fixed" className={styles.root}>
    <Toolbar className={styles.toolbar} disableGutters>
      <div className={styles.logo}>
        <Link href={guiBase}>
          <img alt="The NOMAD logo" className={styles.logoImg} src={`${guiBase}/${oasis ? 'nomad-oasis.png' : 'nomad.png'}`}></img>
        </Link>
      </div>
      <div className={styles.navigation}>
        <div style={{display: 'flex'}}>
          <MainMenu />
          {newGuiUrl && <Tooltip title="Press here to open the new graphical user interface of NOMAD">
            <Button
              size="small"
              variant="outlined"
              color="primary"
              href={newGuiUrl}
              className={styles.newGuiButton}
            >Open new GUI</Button>
          </Tooltip>}
        </div>
        <Breadcrumbs className={styles.crumbs}/>
      </div>
      <div className={styles.actions}>
        <LoginLogout color="primary" classes={{button: styles.menuItem}} />
        <UnitMenu className={styles.menuItem} />
      </div>
    </Toolbar>
    <LoadingIndicator className={styles.progress}/>
  </MuiAppBar>
}
