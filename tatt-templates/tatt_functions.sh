#!/bin/bash

tatt_json_report_error() {
  echo -e "\t\t\"failure_str\": \"${1}\"," >> "${TATT_REPORTFILE}"
}

function tatt_pkg_error
{
  local eout=${2}

  CP=${1#=}
  BUILDDIR=/var/tmp/portage/${CP}
  BUILDLOG=${BUILDDIR}/temp/build.log
  if [[ -n ${TATT_BUILDLOGDIR} && -s ${BUILDLOG} ]]; then
    mkdir -p "${TATT_BUILDLOGDIR}"
    LOGNAME=$(mktemp -p "${TATT_BUILDLOGDIR}" "${CP/\//_}_${TATT_TEST_TYPE}_XXXXX")
    mv "${BUILDLOG}" "${LOGNAME}"
    echo -e "\t\t\"log_file\": \"${LOGNAME}\"," >> "${TATT_REPORTFILE}"

    TESTLOGS=($(find ${BUILDDIR}/work -iname '*test*log*'))
    if [ ${#TESTLOGS[@]} -gt 0 ]; then
      tar cf "${LOGNAME}.tar" "${TESTLOGS[@]}"
      echo -e "\t\t\"extra_logs\": \"${LOGNAME}.tar\"," >> "${TATT_REPORTFILE}"
    fi
  fi

  if [[ "${eout}" =~ REQUIRED_USE ]] ; then
    tatt_json_report_error "REQUIRED_USE not satisfied (probably)"
  elif [[ "${eout}" =~ USE\ changes ]] ; then
    tatt_json_report_error "USE dependencies not satisfied (probably)"
  elif [[ "${eout}" =~ keyword\ changes ]]; then
    tatt_json_report_error "unkeyworded dependencies (probably)"
  elif [[ "${eout}" =~ Error:\ circular\ dependencies: ]]; then
    tatt_json_report_error "circular dependencies (probably)"
  elif [[ "${eout}" =~ Blocked\ Packages ]]; then
    tatt_json_report_error "blocked packages (probably)"
  fi
  echo -e "\t\t\"result\": false" >> "${TATT_REPORTFILE}"
}

function tatt_test_pkg
{
  echo -e '\t{' >> "${TATT_REPORTFILE}"
  trap "echo -e '\t},' >> \"${TATT_REPORTFILE}\"" RETURN
  echo -e "\t\t\"date\": \"$(date)\"," >> "${TATT_REPORTFILE}"

  if [ "${1:?}" == "--test" ]; then
    shift

    # Do a first pass to avoid circular dependencies
    # --onlydeps should mean we're avoiding (too much) duplicate work
    USE="${USE} minimal -doc" emerge --onlydeps -q1 --with-test-deps ${TATT_EMERGEOPTS} "${1:?}" 2>&1 1>/dev/null

    if ! emerge --onlydeps -q1 --with-test-deps ${TATT_EMERGEOPTS} "${1:?}" 2>&1 1>/dev/null; then
      echo -e "\t\t\"atom\": \"${1}\"," >> "${TATT_REPORTFILE}"
      tatt_json_report_error "merging test dependencies failed"
      return 1
    fi
    TFEATURES="${FEATURES} test"
  else
    TFEATURES="${FEATURES}"
  fi
  echo -e "\t\t\"atom\": \"${1}\"," >> "${TATT_REPORTFILE}"
  echo -e "\t\t\"useflags\": \"${USE}\"," >> "${TATT_REPORTFILE}"
  echo -e "\t\t\"features\": \"${TFEATURES}\"," >> "${TATT_REPORTFILE}"

  # --usepkg-exclude needs the package name, so let's extract it
  # from the atom we have
  local name=$(portageq pquery "${1:?}" -n)

  eout=$( FEATURES="${TFEATURES}" emerge -1 --getbinpkg=n --usepkg-exclude="${name}" ${TATT_EMERGEOPTS} "${1:?}" 2>&1 1>/dev/null )
  if [[ $? == 0 ]] ; then
    echo -e "\t\t\"result\": true" >> "${TATT_REPORTFILE}"
  else
    tatt_pkg_error "${1:?}" "${eout}"
    return 1
  fi
}
