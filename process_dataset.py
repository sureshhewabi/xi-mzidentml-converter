import argparse
import sys
import os
import socket
import requests
import time
import ftplib
from urllib.parse import urlparse
import logging
import gc
import shutil

from parser.MzIdParser import MzIdParser
from parser.writer import Writer

from db_config_parser import get_conn_str


def main(args):
    if args.temp:
        temp_dir = os.path.expanduser(args.temp)
    else:
        temp_dir = os.path.expanduser('~/mzId_convertor_temp')

    if args.pxid:
        px_accessions = args.pxid
        for px_accession in px_accessions:
            convert_pxd_accession(px_accession, temp_dir, args.dontdelete)
    elif args.ftp:
        ftp_url = args.ftp
        if args.project_identifier_to_use:
            project_identifier = args.project_identifier_to_use
        else:
            parsed_url = urlparse(ftp_url)
            project_identifier = parsed_url.path.rsplit("/", 1)[-1]
        convert_from_ftp(ftp_url, temp_dir, project_identifier, args.dontdelete)
    else:
        local_dir = args.dir
        if args.project_identifier_to_use:
            project_identifier = args.project_identifier_to_use
        else:
            project_identifier = local_dir.path.rsplit("/", 1)[-1]
        convert_dir(local_dir, project_identifier)


def convert_pxd_accession(px_accession, temp_dir, dont_delete=False):
    # get ftp location from PX
    px_url = 'https://proteomecentral.proteomexchange.org/cgi/GetDataset?ID=' + px_accession + '&outputMode=JSON'
    print('GET request to ProteomeExchange: ' + px_url)
    px_response = requests.get(px_url)
    r = requests.get(px_url)
    if r.status_code == 200:
        print('ProteomeExchange returned status code 200')
        px_json = px_response.json()
        ftp_url = None
        for dataSetLink in px_json['fullDatasetLinks']:
            # name check is necessary because some things have wrong acc, e.g. PXD006574
            if dataSetLink['accession'] == "MS:1002852" or dataSetLink['name'] == "Dataset FTP location":
                ftp_url = dataSetLink['value']
                convert_from_ftp(ftp_url, temp_dir, px_accession, dont_delete)
                break
        if not ftp_url:
            raise Exception('Error: Dataset FTP location not found in ProteomeXchange response')
    else:
        raise Exception('Error: ProteomeXchange returned status code ' + str(px_response.status_code))


def convert_from_ftp(ftp_url, temp_dir, project_identifier, dont_delete):
    if not ftp_url.startswith('ftp://'):
        raise Exception('Error: FTP location must start with ftp://')
    if not os.path.isdir(temp_dir):
        try:
            os.mkdir(temp_dir)
        except OSError as e:
            print('Failed to create temp directory ' + temp_dir)
            print('Error: ' + e.strerror)
            raise e
    print('FTP url: ' + ftp_url)
    parsed_url = urlparse(ftp_url)
    path = os.path.join(temp_dir, project_identifier)
    try:
        os.mkdir(path)
    except OSError:
        pass
    ftp_ip = socket.getaddrinfo(parsed_url.hostname, 21)[0][4][0]
    files = get_ftp_file_list(ftp_ip, parsed_url.path)
    for f in files:
        # check file not already in temp dir
        if not (os.path.isfile(os.path.join(path, f))
                or f.lower == "generated"  # dunno what these files are but they seem to make ftp break
                or f.lower().endswith('raw')
                or f.lower().endswith('raw.gz')
                or f.lower().endswith('all.zip')):
            print('Downloading ' + f + ' to ' + path)
            ftp = get_ftp_login(ftp_ip)
            try:
                ftp.cwd(parsed_url.path)
                ftp.retrbinary("RETR " + f, open(os.path.join(path, f), 'wb').write)
                ftp.quit()
            except ftplib.error_perm as e:
                ftp.quit()
                # error_msg = "%s: %s" % (f, e.args[0])
                # self.logger.error(error_msg)
                raise e
    convert_dir(path, project_identifier)
    if not dont_delete:
        # remove downloaded files
        try:
            shutil.rmtree(path)
        except OSError as e:
            print('Failed to delete temp directory ' + path)
            print('Error: ' + e.strerror)
            raise e


def get_ftp_login(ftp_ip):
    time.sleep(10)
    try:
        ftp = ftplib.FTP(ftp_ip)
        ftp.login()  # Uses password: anonymous@
        return ftp
    except ftplib.all_errors as e:
        print('FTP fail at ' + time.strftime("%c"))
        raise e


def get_ftp_file_list(ftp_ip, ftp_dir):
    ftp = get_ftp_login(ftp_ip)
    try:
        ftp.cwd(ftp_dir)
    except ftplib.error_perm as e:
        error_msg = "%s: %s" % (ftp_dir, e.args[0])
        print(error_msg)
        ftp.quit()
        raise e
    try:
        filelist = ftp.nlst()
    except ftplib.error_perm as resp:
        if str(resp) == "550 No files found":
            print("FTP: No files in this directory")
        else:
            error_msg = "%s: %s" % (ftp_dir, ftplib.error_perm.args[0])
            print(error_msg)
        raise resp
    ftp.close()
    return filelist


def convert_dir(local_dir, project_identifier):
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    #  iterate over files in local_dir
    for file in os.listdir(local_dir):
        if file.endswith(".mzid") or file.endswith(".mzid.gz"):
            print("Processing " + file)
            conn_str = get_conn_str()
            writer = Writer(conn_str, pxid=project_identifier)
            id_parser = MzIdParser(os.path.join(local_dir, file), local_dir, local_dir, writer, logger)
            try:
                id_parser.parse()
                # print(id_parser.warnings + "\n")
            except Exception as e:
                raise e
            gc.collect()
        else:
            continue


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Process mzIdentML files in a dataset and load them into a relational database.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-p', '--pxid', nargs='+',
                       help='proteomeXchange accession, should be of the form PXDnnnnnn or numbers only', )
    group.add_argument('-f', '--ftp',
                       help='process files from specified ftp location, e.g. ftp://ftp.jpostdb.org/JPST001914/')
    group.add_argument('-d', '--dir',
                       help='process files in specified local directory, e.g. /home/user/data/JPST001914')
    parser.add_argument('-i', '--identifier',
                        help='identifier to use for dataset (if providing '
                             'proteome exchange accession these are always used instead and this arg is ignored)')
    parser.add_argument('--dontdelete', action='store_true', help='Do not delete downloaded data after processing')
    parser.add_argument('-t', '--temp', action='store_true', help='Temp folder to download data files into')
    try:
        main(parser.parse_args())
        sys.exit(0)
    except Exception as ex:
        print(ex)
        sys.exit(1)