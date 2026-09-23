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
import PropTypes from 'prop-types'
import { Route } from 'react-router'
import { isEmpty } from 'lodash'
import { CacheRoute, CacheSwitch } from 'react-router-cache-route'
import { matchPath, useLocation, Redirect, useHistory, Link as RouterLink } from 'react-router-dom'
import { Button, Link, makeStyles, Tooltip } from '@material-ui/core'
import About from '../About'
import { MetainfoPage, help as metainfoHelp } from '../archive/MetainfoBrowser'
import EntryPage, { help as entryHelp } from '../entry/EntryPage'
import UploadsPage, { help as uploadsHelp } from '../uploads/UploadsPage'
import UserdataPage, { help as userdataHelp } from '../UserdataPage'
import APIs from '../APIs'
import SearchPage from '../search/SearchPage'
import { SearchContext } from '../search/SearchContext'
import NorthPage, {help as NORTHHelp} from '../north/NorthPage'
import { appBase, encyclopediaBase, apps, northEnabled } from '../../config'
import EntryQuery from '../entry/EntryQuery'
import ResolvePID from '../entry/ResolvePID'
import DatasetPage, { help as datasetHelp } from '../dataset/DatasetPage'
import DatasetsPage, { help as datasetsHelp } from '../dataset/DatasetsPage'
import ResolveDOI from '../ResolveDOI'
import { DatatableExamples } from '../datatable/DatatableExamples'
import {EditQuantityExamples} from '../editQuantity/EditQuantityExamples'
import UploadPage from '../uploads/UploadPage'
import {PlotExamples} from '../archive/PlotExamples'

/**
 * Each route is an object with possible nested sub routes. Therefore, each object only
 * represents one segment of a full path (from root to sub route). Of course sub routes
 * can be re-used by putting them into variables and inserting them at multiple places.
 * Sub routes are matched before there parents. This way, react routers `exact` parameter
 * can be mostly omitted.
 *
 * The following keys are possible:
 * @param {string} path The path segment for this route.
 * @param {array} routes The sub routes.
 * @param {string} breadcrumb An optional breadcrumb. The string is used verbatim.
 * @param {boolean} exact Is passed to react-router routes. Does not match paths that are longer.
 * @param {string} menu This is only allowed on the first two levels. The string is used
 *   verbatim to create menu and menu items respectively.
 * @param {string} tooltip Used as tooltip titles for menu items.
 * @param {object} help  Optional object with string values keys `title` and `help`.
 *   Used for the help dialog.
 * @param {Component} component A react component that will be rendered if this route is
 *   matching the current location. Alternative for render.
 * @param {func} render This is used if this route is matching the current location.
 *   Alternative for component.
 * @param {string} href This can be used for menu items that are not used for navigating
 *   in the app, but linking to other web sites.
 */

/**
 * The reusable sub routes for entries.
 */
const entryRoutes = [
  {
    path: 'entry',
    routes: [
      {
        path: 'id/:entryId',
        breadcrumb: 'Entry',
        component: EntryPage,
        help: {
          title: 'The entry page',
          content: entryHelp
        },
        routes: [
          {
            path: 'files',
            exact: true,
            breadcrumb: 'Files'
          },
          {
            path: 'data',
            exact: true,
            breadcrumb: 'Data'
          },
          {
            path: 'logs',
            exact: true,
            breadcrumb: 'Logs'
          }
        ]
      },
      {
        path: 'query',
        exact: true,
        component: EntryQuery
      },
      {
        path: 'pid',
        component: ResolvePID
      }
    ]
  }
]

/**
 * The reusable sub routes for datasets
 */
const datasetRoutes = [
  {
    path: 'dataset',
    routes: [
      {
        path: 'id/:datasetId',
        cache: 'always',
        breadcrumb: 'Dataset',
        component: DatasetPage,
        routes: entryRoutes,
        help: {
          title: 'Datasets',
          content: datasetHelp
        }
      },
      {
        path: 'doi',
        component: ResolveDOI
      }
    ]
  }
]

/**
 * The reusable sub routes for uploads
 */
const uploadRoutes = [
  {
    path: 'upload',
    routes: [
      {
        path: 'id/:uploadId',
        breadcrumb: 'Upload',
        component: UploadPage,
        routes: [
          {
            path: 'files',
            exact: true,
            breadcrumb: 'Files'
          },
          ...entryRoutes
        ]
      },
      {
        path: 'doi',
        component: ResolveDOI
      }
    ]
  }
]

const searchRoutes = isEmpty(apps)
  ? [{menu: 'No apps available'}]
  : apps.map((app) => {
    const routeMap = {entries: entryRoutes}
    return {
      path: app.path,
      exact: true,
      cache: 'always',
      menu: app.label,
      tooltip: app.description,
      breadcrumb: app.breadcrumb || app.label,
      category: app.category,
      render: (props) => (
        <SearchContext
          resource={app.resource}
          initialPagination={app.pagination}
          initialColumns={app.columns}
          initialRows={app.rows}
          initialMenu={app.menu}
          initialSearchQuantities={app?.search_quantities}
          initialFiltersLocked={app.filters_locked}
          initialDashboard={app?.dashboard}
          initialSearchSyntaxes={app?.search_syntaxes}
          id={app?.path}
        >
          <SearchPage/>
        </SearchContext>
      ),
      help: {
        title: 'About this page',
        content: app.readme || app.description
      },
      routes: routeMap[app.resource]
    }
  })

/**
 * The list with all routes. This is used to determine the routes for routing, the breadcrumbs,
 * and the main menu.
 */
export const routes = [
  {
    path: 'user',
    exact: true,
    redirect: '/user/uploads',
    menu: 'Publish',
    routes: [
      {
        path: 'uploads',
        exact: true,
        component: UploadsPage,
        menu: 'Uploads',
        tooltip: 'Upload and publish new data',
        breadcrumb: 'Your uploads',
        routes: uploadRoutes,
        help: {
          title: 'How to upload data to NOMAD',
          content: uploadsHelp
        }
      },
      {
        path: 'datasets',
        exact: true,
        component: DatasetsPage,
        menu: 'Datasets',
        tooltip: 'Manage your datasets',
        breadcrumb: 'Your datasets',
        routes: datasetRoutes,
        help: {
          title: 'What are NOMAD datasets',
          content: datasetsHelp
        }
      },
      {
        path: 'search',
        exact: true,
        cache: 'always',
        menu: 'Search your data',
        breadcrumb: 'Search your data',
        tooltip: 'Search the data you have uploaded',
        help: {
          title: 'How to manage your data',
          content: userdataHelp
        },
        component: UserdataPage,
        routes: [...entryRoutes, ...datasetRoutes, ...uploadRoutes]
      }
    ]
  },
  {
    path: 'search',
    redirect: '/search/entries',
    menu: 'Explore',
    routes: searchRoutes
  },
  {
    path: 'analyze',
    menu: 'Analyze',
    routes: [
      {
        path: 'apis',
        exact: true,
        menu: 'APIs',
        tooltip: 'The list of APIs offered by NOMAD',
        breadcrumb: 'APIs',
        component: APIs
      },
      {
        path: 'metainfo',
        menu: 'The NOMAD Metainfo',
        tooltip: 'Browse the NOMAD Metainfo Schema',
        breadcrumb: 'NOMAD Metainfo Browser',
        help: {
          title: 'About the NOMAD metainfo',
          content: metainfoHelp
        },
        component: MetainfoPage
      },
      ...(northEnabled ? [
        {
          path: 'north',
          menu: 'NOMAD Remote Tools Hub',
          tooltip: 'Browse the list of available remote tools.',
          breadcrumb: 'NOMAD Remote Tools Hub',
          help: {
            title: 'NOMAD Remote Tools help page',
            content: NORTHHelp
          },
          component: NorthPage
        }
      ] : [])
    ]
  },
  {
    path: 'about',
    menu: 'About',
    redirect: '/about/information',
    routes: [
      {
        path: 'information',
        exact: true,
        menu: 'Information',
        component: About,
        breadcrumb: 'About NOMAD',
        tooltip: 'Overview of the NOMAD'
      },
      {
        menu: 'Forum',
        href: 'https://matsci.org/c/nomad/',
        tooltip: 'The NOMAD user/developer forum on matsci.org'
      },
      {
        menu: 'FAQ',
        href: 'https://nomad-lab.eu/repository-archive-faqs',
        tooltip: 'Frequently Asked Questions (FAQ)'
      },
      {
        menu: 'Docs',
        href: `${appBase}/docs/index.html`,
        tooltip: 'The full user and developer documentation'
      },
      {
        menu: 'Sources',
        href: 'https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR',
        tooltip: 'NOMAD\'s main Gitlab project'
      }
    ]
  },
  ...datasetRoutes,
  ...entryRoutes,
  ...uploadRoutes,
  {
    path: 'dev/datatable',
    render: () => <DatatableExamples />
  },
  {
    path: 'dev/editquantity',
    render: () => <EditQuantityExamples />
  },
  {
    path: 'dev/plot',
    render: () => <PlotExamples />
  }
]

/**
 * The flattened array of all possible routs with paths starting the the root of the
 * app.
 */
export const allRoutes = []

function addRoute(route, pathPrefix) {
  let path = ''
  if (!(route.path === '' && pathPrefix === '')) {
    path = route.path && `${pathPrefix}/${route.path}`
  }
  if (route.routes) {
    route.routes.forEach(childRoute => addRoute(childRoute, path))
  }
  const fullRoute = {...route, path: path}
  allRoutes.push(fullRoute)
}
routes.forEach(route => addRoute(route, ''))

/**
 * Renders all the apps routes according to `routes`.
 */
const useStyles = makeStyles((theme) => (
  {
    wrapper: {
      height: '100%'
    }
  }
))
export const Routes = React.memo(function Routes() {
  const styles = useStyles()
  return <CacheSwitch>
    {allRoutes
      .filter(route => route.path && (route.component || route.render || route.redirect || route.children))
      .map((route, i) => {
        if (route.redirect) {
          return <Redirect
            key={i}
            path={route.path} exact={route.exact}
            to={route.redirect}
          />
        }
        const Comp = route.cache ? CacheRoute : Route
        return <Comp
          key={i}
          path={route.path}
          exact={route.exact}
          component={route.component}
          render={route.render}
          when={route.cache}
          unmount={false}
          saveScrollPosition={false}
          className={route.cache && styles.wrapper}
        >
          {route.children || undefined}
        </Comp>
      })}
    <Redirect from="/" to="/about/information" />
  </CacheSwitch>
})

/**
 * Some routes are available under different prefix routes. E.g., entries under search,
 * uploads, or datasets. This function computes a full url for such a route, based on
 * the given location. E.g., if location is `/search/entries/...`, getUrl for entries
 * would give `/search/entries/entry/id/someuploadid/someentryid`. If the current location
 * does not contain a possible prefix, the url will be returned without prefix.
 *
 * @param {*} path The partial path that should be the end of the returned url.
 * @param {*} location The current react-router location object, e.g. from `useLocation()`.
 * @returns The full url. E.g., that can be put into react-router `<Link to={...}>`.
 */
export function getUrl(path, location) {
  const commonPathPrefix = path.split('/')[0]
  const pathname = typeof location === 'string' ? location : location?.pathname
  const match = pathname && allRoutes
    .filter(route => route.routes?.some(route => route.path === commonPathPrefix))
    .map(route => matchPath(pathname, route.path)).find(match => match)
  const url = match?.url || ''

  return `${url}/${path}`
}

export const RouteLink = React.forwardRef((props, ref) => {
  const {path, children, ...moreProps} = props
  const location = useLocation()
  const to = getUrl(path, location)
  return <Link component={RouterLink} to={to} {...moreProps}>{children}</Link>
})
RouteLink.propTypes = {
  path: PropTypes.string.isRequired,
  children: PropTypes.node
}

export const RouteButton = React.forwardRef((props, ref) => {
  const {component, onClick, path, ...moreProps} = props
  const location = useLocation()
  const history = useHistory()
  const handleClick = (event) => {
    event.stopPropagation()
    onClick && onClick(event)
    history.push(getUrl(path, location))
  }
  return React.createElement(component || Button, {onClick: handleClick, ...moreProps, ref: ref})
})
RouteButton.propTypes = {
  path: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  component: PropTypes.elementType
}

/**
 * A button that allows to navigate to the entry page under the current route prefix.
 * @param {string} uploadId
 * @param {string} entryId
 * @param {elementType} component The component to use to render the button. Default is Button.
 */
export const EntryButton = React.forwardRef((props, ref) => {
  const {entryId, ...moreProps} = props
  const path = `entry/id/${entryId}`
  return <RouteButton path={path} {...moreProps} ref={ref} />
})
EntryButton.propTypes = {
  entryId: PropTypes.string.isRequired
}

/**
 * A button that allows to navigate to the entry page's archive view under the current route prefix.
 * @param {string} uploadId
 * @param {string} entryId
 * @param {string} path
 * @param {elementType} component The component to use to render the button. Default is Button.
 */
export const ArchiveButton = React.forwardRef((props, ref) => {
  const {uploadId, entryId, path, ...moreProps} = props
  const routePath = `entry/id/${entryId}/data/${path}`
  return <RouteButton path={routePath} {...moreProps} ref={ref} />
})
ArchiveButton.propTypes = {
  uploadId: PropTypes.string.isRequired,
  entryId: PropTypes.string.isRequired,
  path: PropTypes.string
}

/**
 * A button that allows to navigate to the dataset page under the current route prefix.
 * @param {string} datasetId
 * @param {elementType} component The component to use to render the button. Default is Button.
 */
export const DatasetButton = React.forwardRef((props, ref) => {
  const {datasetId, ...moreProps} = props
  const path = `dataset/id/${datasetId}`
  return <RouteButton path={path} {...moreProps} ref={ref} />
})
DatasetButton.propTypes = {
  datasetId: PropTypes.string.isRequired
}

/**
 * A button that allows to navigate to the upload page under the current route prefix.
 * @param {string} uploadId
 * @param {elementType} component The component to use to render the button. Default is Button.
 */
export const UploadButton = React.forwardRef((props, ref) => {
  const {uploadId, ...moreProps} = props
  const path = `upload/id/${uploadId}`
  return <RouteButton path={path} {...moreProps} ref={ref} />
})
UploadButton.propTypes = {
  uploadId: PropTypes.string.isRequired
}

/**
 * A link that allows to navigate to the material page (currently an external link).
 * @param {string} materialId
 */
export const MaterialLink = React.forwardRef(({materialId, ...rest}, ref) => {
  const href = `${encyclopediaBase}/material/${materialId}`
  return <Link href={href} {...rest} />
})

MaterialLink.propTypes = {
  materialId: PropTypes.string.isRequired
}

/**
 * A button that allows to navigate to the material page (currently an external link).
 * @param {string} materialId
 * @param {elementType} component The component to use to render the button. Default is Button.
 */
export const MaterialButton = React.forwardRef(({materialId, component, tooltip, ...rest}, ref) => {
  const href = `${encyclopediaBase}/material/${materialId}`
  const props = component
    ? {href: href, ...rest}
    : {href: href, color: 'primary', ...rest}
  const comp = React.createElement(component || Button, {...props, ref})

  // The portal is disabled for this tooltip because this button causes a
  // navigation that otherwise leaves the popup opened (the Tooltip state does
  // not get updated since the page is cached and a new page is shown
  // immediately).
  if (tooltip) {
    return <Tooltip title={tooltip} PopperProps={{disablePortal: true}}>
      {comp}
    </Tooltip>
  }
  return comp
})

MaterialButton.propTypes = {
  materialId: PropTypes.string.isRequired,
  component: PropTypes.elementType,
  tooltip: PropTypes.string
}
MaterialButton.defaultProps = {
  tooltip: 'View this material in the Encyclopedia'
}

export default Routes
