#!/bin/bash

tatt_json_report_error() {
  echo -e "failure_str: ${1}" >> "${TATT_REPORTFILE}"
}

tatt_pkg_error() {
  local eout=${2}

  local CP=${1#=}
  local BUILDDIR=/var/tmp/portage/${CP}
  local BUILDLOG=${BUILDDIR}/temp/build.log
  if [[ -n ${TATT_BUILDLOGDIR} && -s ${BUILDLOG} ]]; then
    mkdir -p "${TATT_BUILDLOGDIR}"
    local LOGNAME=$(mktemp -p "${TATT_BUILDLOGDIR}" "${CP/\//_}_${TATT_TEST_TYPE}_XXXXX")
    mv "${BUILDLOG}" "${LOGNAME}"
    echo "log_file: ${LOGNAME}" >> "${TATT_REPORTFILE}"

    local TESTLOGS=($(find ${BUILDDIR}/work -iname '*test*log*'))
    if [ ${#TESTLOGS[@]} -gt 0 ]; then
      tar cf "${LOGNAME}.tar" "${TESTLOGS[@]}"
      echo "extra_logs: ${LOGNAME}.tar" >> "${TATT_REPORTFILE}"
    fi
  fi

  if [[ ${eout} =~ REQUIRED_USE ]] ; then
    tatt_json_report_error "REQUIRED_USE not satisfied (probably)"
  elif [[ ${eout} =~ USE\ changes ]] ; then
    tatt_json_report_error "USE dependencies not satisfied (probably)"
  elif [[ ${eout} =~ keyword\ changes ]]; then
    tatt_json_report_error "unkeyworded dependencies (probably)"
  elif [[ ${eout} =~ Error:\ circular\ dependencies: ]]; then
    tatt_json_report_error "circular dependencies (probably)"
  elif [[ ${eout} =~ Blocked\ Packages ]]; then
    tatt_json_report_error "blocked packages (probably)"
  fi
}

tatt_test_pkg() {
  echo >> "${TATT_REPORTFILE}"
  echo "---" >> "${TATT_REPORTFILE}"
  echo "time: $(date -u +"%Y-%m-%d %H:%M:%S")" >> "${TATT_REPORTFILE}"

  if [[ ${1:?} == "--test" ]]; then
    shift

    # Do a first pass to avoid circular dependencies
    # --onlydeps should mean we're avoiding (too much) duplicate work
    USE="${USE} minimal -doc" emerge "${1:?}" --onlydeps -q1 --with-test-deps ${TATT_EMERGEOPTS} 2>&1 1>/dev/null

    if ! emerge "${1:?}" --onlydeps -q1 --with-test-deps ${TATT_EMERGEOPTS} 2>&1 1>/dev/null; then
      echo "atom: ${1}" >> "${TATT_REPORTFILE}"
      tatt_json_report_error "merging test dependencies failed"
      return 1
    fi
    TFEATURES="${FEATURES} test"
  else
    TFEATURES="${FEATURES}"
  fi
  echo "atom: ${1}" >> "${TATT_REPORTFILE}"
  echo "useflags: ${USE}" >> "${TATT_REPORTFILE}"
  echo "features: ${TFEATURES}" >> "${TATT_REPORTFILE}"

  # --usepkg-exclude needs the package name, so let's extract it
  # from the atom we have
  local name=$(portageq pquery "${1:?}" -n)

  eout=$( FEATURES="${TFEATURES}" emerge "${1:?}" -1 --getbinpkg=n --usepkg-exclude="${name}" ${TATT_EMERGEOPTS} 2>&1 1>/dev/null )
  if [[ $? == 0 ]] ; then
    echo "result: true" >> "${TATT_REPORTFILE}"
  else
    echo "result: false" >> "${TATT_REPORTFILE}"
    tatt_pkg_error "${1:?}" "${eout}"
    return 1
  fi
}
