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
import { makeStyles } from '@material-ui/core'
import React from 'react'
import { apiBase, appBase, docsBase } from '../config'
import Markdown from './Markdown'
import AppTokenForm from './AppTokenForm'

const useStyles = makeStyles(theme => ({
  root: {
    padding: theme.spacing(3),
    maxWidth: 1024,
    margin: 'auto',
    width: '100%'
  }
}))

export default function About() {
  const classes = useStyles()

  return <div className={classes.root}>
    <Markdown>{`
      ## NOMAD API

      NOMAD's Application Programming Interface (API) allows you to access NOMAD data
      and functions programatically.

      - [NOMAD API dashboard](${apiBase}/v1/extensions/docs)
      - [How-to-guide for basic usage](${docsBase}/howto/manage/program/api.html)
      - [How-to-guide for Python client library](${docsBase}/howto/manage/program/archive_query.html)

      ###  App token

      Next to the usual access token based on OpenID Connect, we provide an
      [app token](${docsBase}/howto/manage/program/auth.html#app-token) with custom expiration date.
      You may request one via the [API](${apiBase}/v1/extensions/docs) or the following form:
    `}</Markdown>

    <AppTokenForm />

    <Markdown>{`
      ## OPTIMADE

      [OPTIMADE](https://www.optimade.org/) is an
      open API standard for materials science databases. This API can be used to search
      and access NOMAD metadata in a standardized way that can also be applied to many
      [other materials science databses](https://providers.optimade.org/).

      - [OPTIMADE API dashboard](${appBase}/optimade/v1/extensions/docs)
      - [OPTIMADE API overview page](${appBase}/optimade/)

      ## DCAT

      [DCAT](https://www.w3.org/TR/vocab-dcat-2/) is a RDF vocabulary designed to facilitate
      interoperability between data catalogs published on the Web. This API allows you
      access to NOMAD via RDF documents following DCAT. You can access NOMAD entries as
      DCAT Datasets or all NOMAD entries as a DCAT Catalog.

      - [DCAT API dashboard](${appBase}/dcat/)
    `}</Markdown>
  </div>
}
