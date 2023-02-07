#!/usr/bin/env python
#  -*- coding: utf-8 -*-
"""
Copyright (c) 2021 Cisco and/or its affiliates.

This software is licensed to you under the terms of the Cisco Sample
Code License, Version 1.1 (the "License"). You may obtain a copy of the
License at

               https://developer.cisco.com/docs/licenses

All use of the material herein must be in accordance with the terms of
the License. All rights not expressly granted by the License are
reserved. Unless required by applicable law or agreed to separately in
writing, software distributed under the License is distributed on an "AS
IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.


"""

from dotenv import load_dotenv

__author__ = "Gerardo Chaves"
__author_email__ = "gchaves@cisco.com"
__copyright__ = "Copyright (c) 2016-2023 Cisco and/or its affiliates."
__license__ = "Cisco"

from requests_oauthlib import OAuth2Session
import os
import sys
import time
import json
from webexteamssdk import WebexTeamsAPI

# load all environment variables
load_dotenv()

SYNC_SPACE_ID = os.getenv('SYNC_SPACE_ID')

AUTHORIZATION_BASE_URL = 'https://api.ciscospark.com/v1/authorize'
TOKEN_URL = 'https://api.ciscospark.com/v1/access_token'
SCOPE = 'spark:all'
ADMIN_SCOPE = ['spark:all', 'spark-admin:people_read', 'spark-admin:telephony_config_read',
               'spark-admin:organizations_read', 'spark-admin:roles_read']

DISABLE_SSL_VERIFY = False
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
gtokens = {}


# admin_login() retrieves admin token from tokens.json and refreshes if needed
# if token is beyond automatic refreshing with refresh token, it will prompt user
# to run script to re-authenticate
def admin_login():
    global gtokens

    if os.path.exists('tokens.json'):
        with open('tokens.json') as f:
            gtokens = json.load(f)
    else:
        gtokens = None

    if gtokens == None or time.time() > (gtokens['expires_at']+(gtokens['refresh_token_expires_in']-gtokens['expires_in'])):
        # We could not read the token from file or it is so old that even the refresh token is invalid, so we have to
        # trigger a full oAuth flow with user intervention

        print("Stored token has expired and cannot be refreshed, please generate a new admin token by using the login.py script.")
        sys.exit(1)
    else:
        # We read a token from file that is at least younger than the expiration of the refresh token, so let's
        # check and see if it is still current or needs refreshing without user intervention
        validated_tokens = check_token_refresh(gtokens)
        gtokens = validated_tokens
        print("Using stored or refreshed token....")


# check_token_refresh() uses the stored refresh token in tokens.json to refresh the
# stored token if possible and store again.
def check_token_refresh(tokens):
    print("Existing token on file, checking if expired....")
    access_token_expires_at = tokens['expires_at']
    if time.time() > access_token_expires_at:
        print("expired!")
        refresh_token = tokens['refresh_token']
        # make the calls to get new token
        extra = {
            'client_id': os.getenv('CLIENT_ID'),
            'client_secret': os.getenv('CLIENT_SECRET'),
            'refresh_token': refresh_token,
        }
        auth_code = OAuth2Session(os.getenv('CLIENT_ID'), token=tokens)
        new_teams_token = auth_code.refresh_token(TOKEN_URL, **extra)
        print("Obtained new_teams_token: ", new_teams_token)
        # assign new token
        tokens = new_teams_token
        # store away the new token
        with open('tokens.json', 'w') as json_file:
            json.dump(tokens, json_file)
    return tokens

# TODO: read email addresses from exclude_users_by_email.txt and keep those out of the group
# TODO: read deptartment name (partial) from excluded_departments.txt y filter any users with a department whos name contains
# any of those strings


def main():
    global gtokens
    admin_login()

    # read the list of emails of users to exclude from the space even if in directory
    excluded_emails = []
    with open('excluded_users_by_email.txt', 'r') as f:
        for line in f.readlines():
            theEmail = line.rstrip()
            excluded_emails.append(theEmail)

    # read the list of emails of users to exclude from the space even if in directory
    excluded_departments = []
    with open('excluded_departments.txt', 'r') as f:
        for line in f.readlines():
            theDept = line.rstrip()
            excluded_departments.append(theDept)

    # initialize the Webex Python SDK
    api = WebexTeamsAPI(
        access_token=gtokens['access_token'], disable_ssl_verify=DISABLE_SSL_VERIFY)

    # Create a set with all organization user IDs to check for space memberships
    dirSetByID = set()

    # Read the entire directory
    fullDirectory = api.people.list()

    # Evaluate if a user should be allowed to be a member of the general space we are syncing with
    for entry in fullDirectory:
        # Exclude users that are not login enabled
        if (not entry.loginEnabled):
            print(f'Filtered out {entry.displayName}: not login enabled')
            continue
        # Exclude users who have a pending invite to webex messaging
        if (entry.invitePending):
            print(f'Filtered out {entry.displayName}: invite pending')
            continue
        # Exclude users who's email address is in the excluded_users_by_email.txt file
        if hasattr(entry, 'emails') and (entry.emails[0] in excluded_emails):
            print(f'Filtered out {entry.displayName}: excluded email address')
            continue
        # Exclude users who's department contains a string in the excluded_departments.txt file
        if hasattr(entry, 'department') and any(dept in entry.department for dept in excluded_departments):
            print(f'Filtered out {entry.displayName}: excluded department')
            continue
        # User passed all criteria, add to the list of users that should be in space
        dirSetByID.add(entry.id)

    # initialize the set variables to use to calculate membership additions or deletions
    spaceSetByID = set()
    membershipDictByID = {}

    print(
        f'Evaluating membership in space with space ID {SYNC_SPACE_ID}')

    # populate the membership set (spaceSetByID) and the dict to map user IDs to membership ID for easy removing if neccesary (membershipDictByID)
    theMembershipList = api.memberships.list(roomId=SYNC_SPACE_ID)
    for aMembership in theMembershipList:
        spaceSetByID.add(aMembership.personId)
        membershipDictByID[aMembership.personId] = aMembership.id

    # create a set of users that are in the org directory but not members of the general space to add them later
    toAdd = dirSetByID.difference(spaceSetByID)
    print(f'Will add the following user IDs: {toAdd}')

    # create a set of users that are not in the org directory but are members of the general space to remove them later
    toRemove = spaceSetByID.difference(dirSetByID)
    print(f'Will remove the following user IDs: {toRemove}')

    # remove users not in the org directory from from the general space if there
    for remUser in toRemove:
        print('Removing user ID: ', remUser)
        api.memberships.delete(membershipDictByID[remUser])

    # add users in the org directory that are not yet in the general space
    for addUser in toAdd:
        print('Adding user ID: ', addUser)
        api.memberships.create(roomId=SYNC_SPACE_ID, personId=addUser)

    print('Done! Space membership and directory are in sync.')


if __name__ == "__main__":
    main()
