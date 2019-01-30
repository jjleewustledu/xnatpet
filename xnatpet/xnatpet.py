import pyxnat

class StageXnat(object):
    __author__ = "John J. Lee"
    __copyright__ = "Copyright 2019"

    sleep_duration = 600 # secs

    def stage_project(self):
        for s in self.fetch_subjects():
            self.stage_subject(s)
        return

    def stage_subject(self, sbj):
        for s in self.fetch_sessions(sbj):
            self.stage_session(s)
        return

    def stage_session(self, ses):
        for s in self.fetch_scans(ses):
            self.stage_scan(s)
        self.stage_rawdata(ses)
        return

    # Fluorodeoxyglucose, Oxygen-water, Oxygen, Carbon monoxide
    def stage_rawdata(self, ses, tracer='Fluorodeoxyglucose'):
        while self.on_schedule() and not self.__resources_available():
            self.__wait()
        d = self.stage_dicoms_rawdata(ses)
        if d:
            self.stage_bfiles_rawdata(ses, d, tracer)
        return d

    def stage_scan(self, scn):
        while self.on_schedule() and not self.__resources_available():
            self.__wait()
        self.stage_dicoms(scn)
        return

    def stage_dicoms(self, scn):
        d = scn.resource('DICOM').files().get()
        self.__download_dicoms(d)
        return d

    def stage_dicoms_rawdata(self, ses, the_files='*.dcm'):
        d = ses.resources().files(the_files).get()
        self.__download_rawdata(d)
        return d

    def stage_bfiles_rawdata(self, ses, the_files='*.dcm', tracer='Fluorodeoxyglucose'):
        d = ses.resources().files(the_files).get()
        #d = self.__select_tracer(d, tracer)
        b = self.__select_bfiles(d)
        self.__download_rawdata(b)
        return b



    # UTILITIES #############################################################################

    def dcm2bf(self, dcm):
        from os import path
        (root,ext) = path.splitext(path.basename(dcm))
        return root + ".bf"

    def fetch_subjects(self, prj):
        if not prj:
            prj = self.project
        return prj.subjects('*').get()

    def fetch_sessions(self, sbj):
        if not sbj:
            sbj = self.subject
        return sbj.experiments('*').get()

    def fetch_scans(self, ses):
        if not ses:
            ses = self.session
        return ses.scans('*').get()

    def dcm_seriesdate(self, dcm):
        d = self.__get_dicom(dcm)
        return d.SeriesDate # yyyymmdd

    def dcm_seriestime(self, dcm):
        d = self.__get_dicom(dcm)
        return d.SeriesTime # hhmmss.ffffff after http://dicom.nema.org/medical/dicom/current/output/chtml/part05/sect_6.2.html

    def ifh_imageduration(self, dcm):
        lm_dict = self.__get_interfile(dcm)
        return lm_dict['image duration']['value'] # sec

    def ifh_studydate(self, dcm):
        lm_dict = self.__get_interfile(dcm)
        return lm_dict['study date']['value'] # yyyy:mm:dd

    def ifh_studytime(self, dcm):
        lm_dict = self.__get_interfile(dcm)
        return lm_dict['study time']['value'] # hh:mm:ss GMT+00:00

    def ifh_tracer(self, dcm, trac):
        lm_dict = self.__get_interfile(dcm)
        return lm_dict['Radiopharmaceutical']['value']

    def is_norm(self, dcm):
        return self.__is_imagetype3(dcm, 'PET_NORM')

    def is_listmode(self, dcm):
        return self.__is_imagetype3(dcm, 'PET_LISTMODE')

    def is_tracer(self, dcm, tracer):
        return self.ifh_tracer == tracer

    def is_umap(self, dcm):
        d = self.__get_dicom(dcm)
        return d.SeriesDescription == u'Head_MRAC_Brain_HiRes_in_UMAP'

    def on_schedule(self):
        return True

    def session_has_ct(self, ses):
        if not ses:
            ses = self.session
        file_list = ses.scans('2').resources().files('*.dcm').get()
        if not file_list:
            return False
        return '.CT.Head' in file_list[0]



    # CLASS-PRIVATE #########################################################################

    def __download(self):
        """
        See also John Flavin's dcm2ni_wholeSession.py
        :param fname:
        :return f. q. fname in dest:
        """
        import pydicom
        import os
        try:
            cookie = self.__jsession_request()
            scanid_list = self.__get_scanid_list(cookie)

            for scanid in scanid_list:
                print('\nBeginning process for scan %s.' % scanid)
                skip_scan = False

                scan_resources = self.__get_scan_resources(cookie, scanid)
                dcm_resource_list = [res for res in scan_resources if res["label"] == "DICOM"]
                if len(dcm_resource_list) == 0:
                    print("Scan %s has no DICOM resource. Skipping." % scanid)
                    continue
                elif len(dcm_resource_list) > 1:
                    print("Scan %s has more than one DICOM resource. Skipping." % scanid)
                    continue
                dicom_resource = dcm_resource_list[0]
                if int(dicom_resource["file_count"]) == 0:
                    print("DICOM resource for scan %s has no files. Skipping." % scanid)
                    continue

                # download DICOMs
                print("Downloading files for scan %s." % scanid)
                dcmdict, dcmdir = self.__prep_dicomdict(cookie, scanid)
                os.chdir(dcmdir)
                for j, (name, path_dict) in enumerate(dcmdict.iteritems()):
                    skip_scan = False

                    if os.access(path_dict['absolutePath'], os.R_OK):
                        self.__symlink(name, path_dict)
                    else:
                        with open(name, 'wb') as f:
                            r = self.__get_url(path_dict['URI'], headers=cookie, verify=False, stream=True)
                            if not r.ok:
                                print("Could not download file %s. Skipping scan %s." % (name, scanid))
                                skip_scan = True
                                continue  # break out of file download loop
                            for block in r.iter_content(1024):
                                if not block:
                                    break
                                f.write(block)
                        print('Downloaded file %s.' % name)

                    if j == 0 and not skip_scan:
                        dcm = pydicom.dcmread(name)
                        modality_header = dcm.get((0x0008, 0x0060), None)
                        if modality_header:
                            skip_scan = self.__check_skip_scan(name, modality_header)
                            if skip_scan:
                                print('Scan %s is a secondary capture. Skipping.' % scanid)
                                continue  # break out of file download loop
                        else:
                            print('Could not read modality from DICOM headers. Skipping.')
                            skip_scan = True
                            continue  # break out of file download loop

                    path_dict['localPath'] = os.path.join(dcmdir, name)

                os.chdir(self.builddir)
                if skip_scan:
                    continue # break out of the rest of the processing for scanid
                print('Done downloading scan %s.\n' % scanid)

            self.__jsession_expire(cookie)

        except (AttributeError, TypeError):
            raise AssertionError('fname must be a filename; dest must be a directory')
        return

    def __download_rawdata(self, fnames):
        """
        See also John Flavin's dcm2ni_wholeSession.py
        :param fname:
        """
        import os
        try:
            cookie = self.__jsession_request()
            for fname in fnames:

                # download Resource
                print('\nBeginning process for session %s rawdata.' % self.session.get()[0])
                print("Downloading file %s." % fname)
                rddict, rddir = self.__prep_rawdatadict(cookie)
                os.chdir(rddir)
                for j, (name, path_dict) in enumerate(rddict.iteritems()):

                    if name == unicode(fname, 'utf-8'):
                        if os.access(path_dict['absolutePath'], os.R_OK):
                            self.__symlink(name, path_dict)
                        else:
                            with open(name, 'wb') as f:
                                r = self.__get_url(path_dict['URI'], headers=cookie, verify=False, stream=True)
                                if not r.ok:
                                    print("Could not download file %s." % name)
                                    break
                                for block in r.iter_content(1024):
                                    if not block:
                                        break
                                    f.write(block)
                            print('Downloaded file %s.' % name)
                        path_dict['localPath'] = os.path.join(rddir, name)

                os.chdir(self.builddir)
                print('Done downloading %s.\n' % name)

            self.__jsession_expire(cookie)

        except (AttributeError, TypeError):
            raise AssertionError('fname must be a filename; dest must be a directory')
        return

    def __check_skip_scan(self, name, modality_header):
        """
        For the first file in the list, we want to check its headers.
        If its modality indicates it is secondary, we don't want to convert the
        series and there is no reason to continue downloading the rest of the files.
        :name:
        :param modality_header:
        :return skip:
        """
        print('Checking modality in DICOM headers of file %s.' % name)
        print('Modality header: %s' % modality_header)
        modality = modality_header.value.strip("'").strip('"')
        skip = modality == 'SC' or modality == 'SR'
        return skip

    def __get_dicom(self, dcm):
        """
        :param dcm:
        :return dcm_datset is a pydicom.dataset.FileDataset containing properties for DICOM fields:
        """
        from pydicom import dcmread
        try:
            dcm_datset = dcmread(dcm)
        except (AttributeError, TypeError):
            raise AssertionError('dcm must be a filename')
        return dcm_datset

    def __get_interfile(self, dcm):
        """
        :param dcm:
        :return lm_dict, a dictionary of interfile fields:
        """
        from interfile import Interfile
        try:
            lm_dict = Interfile.load(dcm)
        except (AttributeError, TypeError):
            raise AssertionError('dcm must be a filename')
        return lm_dict

    def __get_scan_resources(self, cookie, scanid):
        print("Get scan resources for scan %s." % scanid)
        u = self.host + "/data/experiments/%s/scans/%s/resources?format=json" % (self.session.get()[0], scanid)
        r = self.__get_url(u, headers=cookie, verify=False)
        resources = r.json()["ResultSet"]["Result"]
        print('Found resources %s.' % ', '.join(res["label"] for res in resources))
        return resources

    def __get_scanid_list(self, cookie):
        print("Get scan list for session ID %s." % self.session.get()[0])
        u = self.host + "/data/experiments/%s/scans?format=json" % self.session.get()[0]
        r = self.__get_url(u, headers=cookie, verify=False)
        request_result_list = r.json()["ResultSet"]["Result"]
        idl = [scan['ID'] for scan in request_result_list]
        print('Found scans %s.' % ', '.join(idl))
        return idl

    def __get_url(self, url, **kwargs):
        import requests, sys
        try:
            r = requests.get(url, **kwargs)
            r.raise_for_status()
        except (requests.ConnectionError, requests.exceptions.RequestException) as e:
            print("Request Failed")
            print("    " + str(e))
            sys.exit(1)
        return r

    def __is_imagetype3(self, dcm, itype3):
        import pydicom
        try:
            dataset = pydicom.dcmread(dcm)
        except (AttributeError, TypeError):
            raise AssertionError('dcm must be a filename')
        return dataset.ImageType == ['ORIGINAL', 'PRIMARY', itype3]

    def __jsession_request(self):
        r = self.__get_url(self.host + "/data/JSESSION", auth=(self.uid, self.pwd), verify=False)
        cookie = {"Cookie": "JSESSIONID=" + r.content}
        return cookie

    def __jsession_expire(self, cookie):
        import requests
        requests.delete(self.host + "/data/JSESSION", headers=cookie, verify=False)
        return

    def __prep_dicomdict(self, cookie, scanid):
        ddir = self.__prep_dicomdir(scanid)

        # get list of DICOMs
        print('Get list of DICOM files for scan %s.' % scanid)
        u = self.host + "/data/experiments/%s/scans/%s/resources/DICOM/files?format=json" % (self.session.get()[0], scanid)
        r = self.__get_url(u, headers=cookie, verify=False)

        # John Flavin:  "I don't like the results being in a list, so I will build a dict keyed off file name"
        ddict = {dicom['Name']: {'URI': self.host+dicom['URI']} for dicom in r.json()["ResultSet"]["Result"]}

        # have to manually add absolutePath with a separate request
        u = self.host + "/data/experiments/%s/scans/%s/resources/DICOM/files?format=json&locator=absolutePath" % (
            self.session.get()[0], scanid)
        r = self.__get_url(u, headers=cookie, verify=False)
        for dcm in r.json()["ResultSet"]["Result"]:
            ddict[dcm['Name']]['absolutePath'] = self.host+dcm['absolutePath']
        return ddict, ddir

    def __prep_rawdatadict(self, cookie):
        rddir = self.dicomdir

        # get list of DICOMs
        print('Get list of RawData files for session %s.' % self.session.get()[0])
        u = self.host + "/data/experiments/%s/resources/RawData/files?format=json" % self.session.get()[0]
        r = self.__get_url(u, headers=cookie, verify=False)

        # John Flavin:  "I don't like the results being in a list, so I will build a dict keyed off file name"
        rddict = {rd['Name']: {'URI': self.host+rd['URI']} for rd in r.json()["ResultSet"]["Result"]}

        # have to manually add absolutePath with a separate request
        u = self.host + "/data/experiments/%s/resources/RawData/files?format=json&locator=absolutePath" % self.session.get()[0]
        r = self.__get_url(u, headers=cookie, verify=False)
        for rd1 in r.json()["ResultSet"]["Result"]:
            rddict[rd1['Name']]['absolutePath'] = self.host+rd1['absolutePath']
        return rddict, rddir

    def __prep_dicomdir(self, scanid):
        import os
        ddir = os.path.join(self.dicomdir, scanid)
        if not os.access(ddir, os.R_OK):
            print('\nMaking scan DICOM directory %s.' % ddir)
            os.mkdir(ddir)
        # remove any existing files in the builddir;
        # this is unlikely to happen in any environment other than testing.
        for f in os.listdir(ddir):
            os.remove(os.path.join(ddir, f))
        return ddir

    def __resources_available(self):
        return True

    def __select_bfiles(self, dcms):
        """
        examines rawdata dcms and selects bf for norm and listmode data
        :param dcms:
        :return bf:
        """
        bf = []
        for d in dcms:
            if self.is_norm(d) or self.is_listmode(d):
                bf.append(self.dcm2bf(d))
        return bf

    def __select_tracer(self, dcms, tracer='Fluorodeoxyglucose'):
        """
        examines rawdata dcms for given tracer and selects bf for norm and listmode data
        :param dcms:
        :param tracer in ['Fluorodeoxyglucose', 'Oxygen-water', 'Oxygen', 'Carbon monoxide']:
        :return bf:
        """
        bf = []
        for d in dcms:
            if self.is_tracer(d, tracer):
                bf.append(self.dcm2bf(d))
        return bf

    def __symlink(self, name, path_dict):
        import os
        from shutil import copy as fileCopy
        try:
            os.symlink(path_dict['absolutePath'], name)
            print('Made link to %s.' % path_dict['absolutePath'])
        except:
            fileCopy(path_dict['absolutePath'], name)
            print('Copied %s.' % path_dict['absolutePath'])
        return

    def __wait(self):
        import time
        time.sleep(self.sleep_duration)
        return

    def __init__(self, uid, pwd, prj="CCIR_00754", sbj="HYGLY48", ses="CNDA_E249152", scn="*", cch="/work/SubjectsStash"):
        """
        :param uid:
        :param pwd:
        :param prj:
        :param sbj:
        :param ses:
        :param scn:
        :param cch is the preferred cache directory:
        """
        import os
        self.host     = 'https://cnda.wustl.edu'
        self.uid      = uid
        self.pwd      = pwd
        self.dicomdir = cch
        self.xnat     = pyxnat.Interface(self.host, user=self.uid, password=self.pwd, cachedir=self.dicomdir)
        self.project  = self.xnat.select.project(prj)
        self.subject  = self.project.subject(sbj)
        self.session  = self.subject.experiments(ses)
        self.scan     = self.session.scan(scn)
        self.builddir = os.getcwd()
