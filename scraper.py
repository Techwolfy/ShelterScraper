#!/usr/bin/python3

#
# Imports
#

import json
import re
import time
import requests
from lxml import html, etree

import email
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart

#
# Constants
#

PAWS_URL = 'https://www.paws.org/adopt/dogs/'
PETANGO_URL = 'https://www.petango.com/DesktopModules/Pethealth.Petango/Pethealth.Petango.DnnModules.AnimalSearchResult/API/Main/Search'
PETFINDER_URL = 'https://www.petfinder.com/search/'
PETHARBOR_URL = 'http://petharbor.com/results.asp'
UA_HEADER = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:86.0) Gecko/20100101 Firefox/86.0'}

INTERVAL = 60

#
# Globals
#

alertEnabled = False
alertTriggered = False

keys = {
    'petfinderToken': '',
    'emailServer': '',
    'emailFrom': '',
    'emailTo': '',
    'location': '',
    'excludedBreeds': [],
    'breedsPetango': [],
    'sheltersPetango': [],
    'sheltersPetfinder': [],
    'sheltersPetharbor': []
}

seen = {}


#
# Load state from file
#

def loadState():
    global keys
    global seen

    with open('keys.json', 'r') as f:
        raw = f.read()
        parsed = re.sub('#.*', '', raw)
        keys = json.loads(parsed)

    try:
        with open('state.json', 'r') as f:
            state = f.read()
            seen = json.loads(state)

        for provider in seen:
            for shelterId in seen[provider]:
                for animalId in seen[provider][shelterId]:
                    printDog(seen[provider][shelterId][animalId])
                print()

    except FileNotFoundError:
        pass


#
# Save state to file
#

def saveState():
    with open('state.json', 'w') as f:
        f.write(json.dumps(seen))


#
# Display dog information
#

def printDog(dog):
    statusString = ''
    if dog['timeAdopted'] != 0:
        statusTime = dog['timeAdopted'] - dog['timeFound']
        statusString = ' - ADOPTED [in %dd %dh %dm]' % (int(statusTime / 86400), int((statusTime % 86400) / 3600), int((statusTime % 3600) / 60))
    elif dog['pending']:
        statusTime = dog['timePending'] - dog['timeFound']
        statusString = ' - PENDING [in %dd %dh %dm]' % (int(statusTime / 86400), int((statusTime % 86400) / 3600), int((statusTime % 3600) / 60))
    print('%s%s (%s-%s-%s) - %s' % (dog['name'], statusString, dog['provider'], dog['shelterId'], dog['animalId'], dog['breed']))


#
# Send dog information via email
#

def sendDog(dog):
    if dog['photo'] == None or dog['timeAdopted'] != 0 or dog['pending']:
        return

    msg = MIMEMultipart()
    msg['From'] = keys['emailFrom']
    msg['To'] = keys['emailTo']
    msg['Date'] = email.utils.formatdate(localtime=True)

    msgText = '%s (%s-%s-%s)\n%s' % (dog['name'], dog['provider'], dog['shelterId'], dog['animalId'], dog['breed'])
    msg.attach(MIMEText(msgText))

    for f in os.listdir('dogs'):
        if f.startswith(dog['animalId'] + '.') and not f.endswith('.json'):
            with open('dogs/' + f, 'rb') as photo:
                msgPhoto = MIMEImage(photo.read(), name=dog['animalId'])
                msgPhoto['Content-Disposition'] = 'attachment; filename="%s"' % f
                msg.attach(msgPhoto)

    try:
        smtp = smtplib.SMTP_SSL(keys['emailServer'], 465)
        smtp.send_message(msg)
    except:
        print('Failed to send dog %s-%s-%s' % (dog['provider'], dog['shelterId'], dog['animalId']))


#
# Handle new dog
#

def handleDog(provider, shelterId, animalId, name, breed, photoUrl, adoptionPending, data):
    global alertTriggered

    if any(b in breed for b in keys['excludedBreeds']):
        return

    if provider not in seen:
        seen[provider] = {}

    if shelterId not in seen[provider]:
        seen[provider][shelterId] = {}

    animalSeen = False
    animalPending = False
    newPhoto = False
    if animalId in seen[provider][shelterId]:
        animalSeen = True

        if seen[provider][shelterId][animalId]['pending'] == adoptionPending:
            if seen[provider][shelterId][animalId]['photo'] != None or photoUrl == None:
                seen[provider][shelterId][animalId]['timeSeen'] = time.time()
                return
            else:
                newPhoto = True
        elif adoptionPending:
            animalPending = True

    seen[provider][shelterId][animalId] = {
        'animalId': animalId,
        'shelterId': shelterId,
        'name': name,
        'breed': breed,
        'photo': photoUrl,
        'pending': adoptionPending,
        'provider': provider,
        'timeFound': seen[provider][shelterId][animalId]['timeFound'] if animalSeen else time.time(),
        'timePending': time.time() if animalSeen or adoptionPending else 0,
        'timeAdopted': 0,
        'timeSeen': time.time(),
        'data': data
    }

    with open('dogs/%s.json' % animalId, 'w') as f:
        f.write(json.dumps(seen[provider][shelterId][animalId]))
    if photoUrl:
        image = requests.get(photoUrl)
        ext = image.headers['Content-Type'].rsplit('/', 1)[-1]
        with open('dogs/%s.%s' % (animalId, ext), 'wb') as f:
            f.write(image.content)

    if not newPhoto:
        printDog(seen[provider][shelterId][animalId])
    if not animalPending and not newPhoto:
        sendDog(seen[provider][shelterId][animalId])
        alertTriggered = True


#
# Handle adopted dogs
#

def checkDogs():
    now = time.time()

    for provider in seen:
        for shelterId in seen[provider]:
            for animalId in list(seen[provider][shelterId]):
                if any(b in seen[provider][shelterId][animalId]['breed'] for b in keys['excludedBreeds']):
                    seen[provider][shelterId].pop(animalId)
                    continue

                if now - seen[provider][shelterId][animalId]['timeSeen'] < INTERVAL * 4:
                    continue

                dog = seen[provider][shelterId].pop(animalId)
                dog['timeAdopted'] = now

                with open('dogs/%s.json' % dog['animalId'], 'w') as f:
                    f.write(json.dumps(dog))

                printDog(dog)


#
# Make HTTP request
#

def doRequest(provider, shelterId, verb, url, data=None, headers={}):
    status = ''

    try:
        if verb == 'get':
            r = requests.get(url, headers={**headers, **UA_HEADER}, params=data, allow_redirects=False)
            status = str(r.status_code)
        elif verb == 'post':
            r = requests.post(url, headers={**headers, **UA_HEADER}, data=data, allow_redirects=False)
            status = str(r.status_code)
        else:
            return None
    except requests.ConnectionError:
        status = 'ConnectionError'

    if status != '200' or r.text == '':
        print('Request failed: %s, %s, %s' % (status, provider, shelterId))
        for animalId in seen[provider][shelterId]:
            seen[provider][shelterId][animalId]['timeSeen'] = time.time()
        return None

    return r


#
# Search PAWS shelter
#

def runPAWS():
    r = doRequest('PAWS', '0000', 'get', PAWS_URL)
    if r == None:
        return

    h = html.document_fromstring(r.text)
    dogs = h.xpath('//section[@class="cards"]//article')

    for dog in dogs:
        dogData = dog.xpath('.//span[@class="card-block__label"]')

        animalId = dog.xpath('@id')[0].split('-')[1]
        name = dog.xpath('.//h3[@class="card-block__title"]/text()')[0]
        photo = dog.xpath('.//img[@class="card-block__img-animal"]/@src')[0]
        pending = len(dog.xpath('.//span[@class="card-block__pill"]/text()')) != 0
        breed = dogData[1].text if len(dogData) > 2 else 'Breed Unknown'

        handleDog('PAWS',
                  '0000',
                  animalId + '-PAWS',
                  name,
                  breed,
                  photo,
                  pending,
                  etree.tostring(dog, encoding='unicode'))


#
# Search Petango shelters
#

def runPetangoShelter(shelterId):
    search = {
        'speciesId': '1',
        'goodWithDogs': False,
        'goodWithCats': False,
        'goodWithChildren': False,
        'mustHavePhoto': False,
        'mustHaveVideo': False,
        'shelterId': shelterId,
        'happyTails': False,
        'lostAnimals': False,
        'moduleId': 983,
        'recordOffset': 0,
        'recordAmount': 100
    }

    r = doRequest('Petango',
                  str(shelterId),
                  'post',
                  PETANGO_URL,
                  search,
                  {'ModuleId': '983', 'TabId': '278'})
    if r == None:
        return

    dogs = r.json()['items']

    for dog in dogs:
        handleDog('Petango',
                  str(shelterId),
                  str(dog['id']),
                  dog['name'],
                  dog['breed'],
                  dog['photo'],
                  False,
                  dog)


#
# Search Petango by breed
#

def runPetango(location, gender, breedId):
    search = {
        'location': location,
        'distance': '250',
        'speciesId': '1',
        'breedId': breedId,
        'gender': gender,
        'size': '',
        'color': '',
        'goodWithDogs': False,
        'goodWithCats': False,
        'goodWithChildren': False,
        'mustHavePhoto': False,
        'mustHaveVideo': False,
        'declawed': '',
        'happyTails': False,
        'lostAnimals': False,
        'animalId': '',
        'moduleId': 843,
        'recordOffset': 0,
        'recordAmount': 100
    }

    r = doRequest('Petango',
                  '0000',
                  'post',
                  PETANGO_URL,
                  search,
                  {'ModuleId': '843', 'TabId': '260'})
    if r == None:
        return

    dogs = r.json()['items']

    for dog in dogs:
        handleDog('Petango',
                  '0000',
                  str(dog['id']),
                  dog['name'],
                  dog['breed'],
                  dog['photo'],
                  False,
                  dog)


#
# Search Petfinder shelters
#

def runPetfinderShelter(shelterId, page=1):
    search = {
        'page': str(page),
        'limit[]': '100',
        'status': 'adoptable',
        'token': keys['petfinderToken'],
        'distance[]': 'Anywhere',
        'type[]': 'dogs',
        'sort[]': 'recently_added',
        'shelter_id[]': shelterId,
        'include_transportable': 'true'
    }

    r = doRequest('Petfinder',
                  shelterId,
                  'get',
                  PETFINDER_URL,
                  search,
                  {'X-Requested-With': 'XMLHttpRequest'})
    if r == None:
        return

    result = r.json()['result']
    dogs = result['animals']

    for dog in dogs:
        handleDog('Petfinder',
                  shelterId,
                  str(dog['animal']['id']),
                  dog['animal']['name'],
                  dog['animal']['breeds_label'],
                  dog['animal']['primary_photo_url'] if 'primary_photo_url' in dog['animal'] else None,
                  False,
                  dog)

    if page < result['pagination']['total_pages']:
        runPetfinderShelter(shelterId, page + 1)


#
# Search PetHarbor shelters
#

def runPetharbor(shelterId, page=1):
    search = {
        'searchtype': 'ADOPT',
        'start': '3',
        'friends': '1',
        'samaritans': '1',
        'nosuccess': '0',
        'rows': '25',
        'imght': '120',
        'imgres': 'detail',
        'tWidth': '200',
        'view': 'sysadm.v_animal_short',
        'fontface': 'arial',
        'fontsize': '10',
        'miles': '50',
        'shelterlist': '\'' + shelterId + '\'',
        'atype': '',
        'where': 'type_DOG',
        'page': str(page)
    }

    r = doRequest('Petharbor',
                  shelterId,
                  'get',
                  PETHARBOR_URL,
                  search)
    if r == None:
        return

    h = html.document_fromstring(r.text)
    h.make_links_absolute(PETHARBOR_URL)
    dogs = h.xpath('//table[@class="ResultsTable"]//tr')

    for dog in dogs:
        dogData = dog.xpath('.//td/text()')
        if dogData[0] == 'Picture':
            continue

        animalId = dogData[1].rsplit(' ', 1)[1].replace('(', '').replace(')', '')
        name = dogData[1].rsplit(' ', 1)[0]
        photo = dog.xpath('.//img/@src')[0]
        breed = dogData[4]

        handleDog('Petharbor',
                  shelterId,
                  animalId,
                  name,
                  breed,
                  photo,
                  False,
                  etree.tostring(dog, encoding='unicode'))


    if len(h.xpath('//a[text()="Next Page"]')) > 0:
        runPetharbor(shelterId, page + 1)


#
# Alert user
#

def handleAlert():
    global alertEnabled
    global alertTriggered

    if not alertEnabled or not alertTriggered:
        alertEnabled = True
        alertTriggered = False
        return

    print(time.strftime('%c'))

    for i in range(0, 5):
        print('\x07', end='', flush=True)
        time.sleep(0.25)

    print()
    alertTriggered = False


#
# Display waiting spinner
#

def spin(seconds):
    sequence = ['|', '/', '-', '\\']
    for i in range(0, seconds * 4):
        print('%s %s (next update in: %ds)  ' % (sequence[i % 4], time.strftime('%c'), seconds - (i / 4)), end='\r')
        time.sleep(0.25)
    print(' ' * 80, end='\r')


#
# Main function
#

if __name__ == '__main__':
    loadState()

    while True:
        runPAWS()

        for shelter in keys['sheltersPetango']:
            runPetangoShelter(shelter)
        for breed in keys['breedsPetango']:
            runPetango(keys['location'], 'F', breed)

        for shelter in keys['sheltersPetfinder']:
            runPetfinderShelter(shelter)

        for shelter in keys['sheltersPetharbor']:
            runPetharbor(shelter)

        checkDogs()

        handleAlert()
        saveState()
        spin(INTERVAL)
