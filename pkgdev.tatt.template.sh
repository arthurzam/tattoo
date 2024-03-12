#!/bin/bash

main() {
    trap "echo 'signal captured, exiting the entire script...'; exit" SIGHUP SIGINT SIGTERM

    local test_ret=0

    echo '# bug: {{ job_name }}' > "{{ report_file }}"
    echo "# time: $(date -u +"%Y-%m-%d %H:%M:%S")" >> "{{ report_file }}"

    {% for atom, is_test, use_flags in jobs %}
    {% if is_test %}
    TUSE="{{ use_flags }}" tatt_test_pkg '{{ atom }}' --test || test_ret=1
    {% else %}
    TUSE="{{ use_flags }}" tatt_test_pkg '{{ atom }}' || test_ret=1
    {% endif %}
    {% endfor %}

    exit ${test_ret}
}

cleanup() {
    echo "Cleaning up"
    {% for file in cleanup_files %}
    rm -v -f -r '{{ file }}'
    {% endfor %}
    rm -v -f $0
}

tatt_json_report_error() {
    echo -e "failure_str: ${1}" >> "{{ report_file }}"
}

tatt_pkg_error() {
    local eout=${2}

    local CP=${1#=}
    local PORTAGE_TMPDIR=$( pinspect portageq envvar2 / PORTAGE_TMPDIR )
    local BUILDDIR=${PORTAGE_TMPDIR:-/var/tmp}/portage/${CP}
    local BUILDLOG=${BUILDDIR}/temp/build.log
    if [[ -s ${BUILDLOG} ]]; then
        mkdir -p {{ log_dir }}
        local LOGNAME=$( mktemp -p {{ log_dir }} "${CP/\//_}_use_XXXXX" )
        cp "${BUILDLOG}" "${LOGNAME}"
        echo "log_file: ${LOGNAME}" >> "{{ report_file }}"
        readarray -d '' TESTLOGS < <(find "${BUILDDIR}/work" -iname '*test*log*' -print0)
{% raw %}
        if [[ ${#TESTLOGS[@]} -gt 0 ]]; then
            tar cf "${LOGNAME}.tar" "${TESTLOGS[@]}"
            echo "extra_logs: ${LOGNAME}.tar" >> "{{ report_file }}"
        fi
    fi
{% endraw %}

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
    elif [[ ${eout} =~ have\ been\ masked ]]; then
        tatt_json_report_error "masked packages (probably)"
    fi
}

tattoo_emerge() {
    emerge "$@" {{ emerge_opts }} 2>&1 1>${EMERGE_OUTPUT:?}
}

tatt_test_pkg() {
    echo >> "{{ report_file }}"
    echo "---" >> "{{ report_file }}"
    echo "time: $(date -u +"%Y-%m-%d %H:%M:%S")" >> "{{ report_file }}"
    echo "atom: ${1:?}" >> "{{ report_file }}"
    echo "useflags: ${TUSE}" >> "{{ report_file }}"

    local CP=${1#=}
    CP=${CP/\//_}

    local EMERGE_OUTPUT=/dev/null
    if [[ -t 1 ]]; then
        EMERGE_OUTPUT=/dev/tty
    fi

    if [[ ${2} == "--test" ]]; then
        # Do a first pass to avoid circular dependencies
        # --onlydeps should mean we're avoiding (too much) duplicate work
        USE="minimal -doc" tattoo_emerge "${1}" --onlydeps --quiet --oneshot --with-test-deps

        if ! tattoo_emerge "${1}" --onlydeps --quiet --oneshot --with-test-deps; then
            tatt_json_report_error "merging test dependencies failed"
            return 1
        fi
        printf "%s pkgdev_tatt_{{ job_name }}_test\n" "${1}"> "/etc/portage/package.env/pkgdev_tatt_{{ job_name }}/${CP}"
        echo "features: test" >> "{{ report_file }}"
    else
        printf "%s pkgdev_tatt_{{ job_name }}_no_test\n" "${1}" > "/etc/portage/package.env/pkgdev_tatt_{{ job_name }}/${CP}"
        echo "features: " >> "{{ report_file }}"
    fi
    {% for env in extra_env_files %}
    printf "%s {{env}}\n" "${1}" >> "/etc/portage/package.env/pkgdev_tatt_{{ job_name }}/${CP}"
    {% endfor %}

    printf "%s %s\n" "${1}" "${TUSE}" > "/etc/portage/package.use/pkgdev_tatt_{{ job_name }}/${CP}"

    # --usepkg-exclude needs the package name, so let's extract it
    # from the atom we have
    local name=$( pquery --no-version "${1}" )

    eout=$( tattoo_emerge "${1}" --oneshot --getbinpkg=n --usepkg-exclude="${name}" )
    local RES=$?

    rm -v -f /etc/portage/package.{env,use}/pkgdev_tatt_{{ job_name }}/${CP}

    if [[ ${RES} -eq 0 ]] ; then
        echo "result: true" >> "{{ report_file }}"
    else
        echo "result: false" >> "{{ report_file }}"
        tatt_pkg_error "${1}" "${eout}"
        return 1
    fi
}

if [[ ${1} == "--clean" ]]; then
    cleanup
else
    main
fi
