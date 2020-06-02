#! /bin/bash

set -e

echo "VARIABLES"
echo "CI_COMMIT_TAG=${CI_COMMIT_TAG}"
echo "CI_API_V4_URL=${CI_API_V4_URL}"
echo "CI_PROJECT_ID=${CI_PROJECT_ID}"
echo "CI_PIPELINE_ID=${CI_PIPELINE_ID}"
echo "CI_PROJECT_URL=${CI_PROJECT_URL}"

# The release notes for this version should be the first line of the CHANGELOG.
if [[ $(head -n 1 CHANGELOG.rst) == "${CI_COMMIT_TAG}" ]]; then
    # Extract all of the bullet points and other things until the next header.
    i=0
    first=1
    while read l; do
        i=$(( $i + 1 ))
        if [[ $l =~ ^=+$ ]]; then
            if [[ $first == 0 ]]; then
                break
            fi
            first=0
        fi

        if [[ "${l:0:11}" == ":Milestone:" ]]; then
            milestone="$(echo $l | sed -n 's/:Milestone: \(.*\)/\1/p')"
        fi
    done < CHANGELOG.rst

    # i is now the index of the line below the second header.

    description="**Release Notes:**

$(head -n $(( $i - 2 )) CHANGELOG.rst | tail -n $(( $i - 5 )))"
fi

if [[ "${description}" == "" ]]; then
    description="No description provided for this release."
fi

description=$(echo "$description" | rst2html5 --no-indent --template "{body}" | sed -e 's/\"/\\\"/g')

milestones=""
if [[ "${milestone}" != "" ]]; then
    milestones=",\"milestones\":[\"${milestone}\"]"
fi

url="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/releases"
data="
{
    \"name\": \"${CI_COMMIT_TAG}\",
    \"tag_name\": \"${CI_COMMIT_TAG}\",
    \"description\": \"${description}\",
    ${milestones}
    \"assets\": {
        \"links\": [
            {
                \"name\": \"offlinemsmtp-${CI_COMMIT_TAG:1}.tar.gz\",
                \"url\": \"${CI_PROJECT_URL}/-/jobs/artifacts/${CI_COMMIT_TAG}/raw/dist/offlinemsmtp-${CI_COMMIT_TAG:1}.tar.gz?job=build\"
            }
        ]
    }
}
"

echo "URL:"
echo "$url"
echo "DATA:"
echo "$data"

curl \
    --header 'Content-Type: application/json' \
    --header "PRIVATE-TOKEN: ${RELEASE_PUBLISH_TOKEN}" \
    --data "$data" \
    --request POST \
    $url
