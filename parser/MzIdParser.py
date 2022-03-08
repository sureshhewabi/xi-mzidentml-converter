from pyteomics import mzid
import re
import ntpath
import json
from time import time
from parser.peaklistReader.PeakListWrapper import PeakListWrapper
import zipfile
import gzip
import os
from .NumpyEncoder import NumpyEncoder
import obonet


class MzIdParseException(Exception):
    pass


class MzIdParser:
    """Class for parsing identification data from mzIdentML."""

    def __init__(self, mzid_path, temp_dir, peak_list_dir, writer, logger):
        """
        Initialise the Parser.

        :param mzid_path: path to mzidentML file
        :param temp_dir: absolute path to temp dir for unzipping/storing files
        :param peak_list_dir: path to the directory containing the peak list file(s)
        :param writer: result writer
        :param logger: logger
        """
        self.search_modifications = None
        self.mzid_path = mzid_path

        self.peak_list_readers = {}  # peak list readers indexed by spectraData_ref
        self.temp_dir = temp_dir
        if not self.temp_dir.endswith('/'):
            self.temp_dir += '/'
        self.peak_list_dir = peak_list_dir
        if peak_list_dir and not peak_list_dir.endswith('/'):
            self.peak_list_dir += '/'

        self.writer = writer
        self.logger = logger

        self.ms_obo = obonet.read_obo(
            'https://raw.githubusercontent.com/HUPO-PSI/psi-ms-CV/master/psi-ms.obo')

        # ToDo:
        # From mzidentML schema 1.2.0:
        # <SpectrumIdentificationProtocol> must contain the CV term 'cross-linking search'
        # (MS:1002494)
        self.contains_crosslinks = False

        self.warnings = []
        self.write_new_upload()  # overridden (empty function) in xiSPEC subclass

        # init self.mzid_reader (pyteomics mzid reader)
        if self.mzid_path.endswith('.gz') or self.mzid_path.endswith('.zip'):
            self.mzid_path = MzIdParser.extract_mzid(self.mzid_path)

        self.logger.info('reading mzid - start ' + self.mzid_path)
        start_time = time()
        # schema:
        # https://raw.githubusercontent.com/HUPO-PSI/mzIdentML/master/schema/mzIdentML1.2.0.xsd
        try:
            self.mzid_reader = mzid.MzIdentML(self.mzid_path, retrieve_refs=False)
        except Exception as e:
            raise MzIdParseException(type(e).__name__, e.args)

        self.logger.info('reading mzid - done. Time: {} sec'.format(round(time() - start_time, 2)))

        self.upload_info()  # overridden (empty function) in xiSPEC subclass

    # used by TestLoop when downloading files from PRIDE
    # def get_supported_peak_list_file_names(self):
    #     """
    #     :return: list of all supported peak list file names
    #     """
    #     peak_list_file_names = []
    #     for spectra_data_id in self.mzid_reader._offset_index["SpectraData"].keys():
    #         sp_datum = self.mzid_reader.get_by_id(spectra_data_id, tag_id='SpectraData',
    #                                               detailed=True)
    #         ff_acc = sp_datum['FileFormat']['accession']
    #         if any([ff_acc == 'MS:1001062',  # MGF
    #                 ff_acc == 'MS:1000584',  # mzML
    #                 ff_acc == 'MS:1001466',  # ms2
    #                 ]):
    #             peak_list_file_names.append(ntpath.basename(sp_datum['location']))
    #
    #     return peak_list_file_names

    # # used by TestLoop when downloading files from PRIDE
    # def get_all_peak_list_file_names(self):
    #     """
    #     :return: list of all peak list file names
    #     """
    #     peak_list_file_names = []
    #     for spectra_data_id in self.mzid_reader._offset_index["SpectraData"].keys():
    #         sp_datum = self.mzid_reader.get_by_id(spectra_data_id, tag_id='SpectraData')
    #         peak_list_file_names.append(ntpath.basename(sp_datum['location']))
    #
    #     return peak_list_file_names

    def parse(self):
        """Parse the file."""
        start_time = time()

        if self.peak_list_dir:
            self.init_peak_list_readers()

        self.parse_analysis_protocol_collection()
        self.parse_db_sequences()  # overridden (empty function) in xiSPEC subclass
        self.parse_peptides()
        self.parse_peptide_evidences()
        self.main_loop()

        self.fill_in_missing_scores()  # empty here, overridden in xiSPEC subclass to do stuff
        self.write_other_info()  # overridden (empty function) in xiSPEC subclass

        self.logger.info('all done! Total time: ' + str(round(time() - start_time, 2)) + " sec")

    @staticmethod
    def check_spectra_data_validity(sp_datum):
        # is there anything we'd like to complain about?
        # SpectrumIDFormat
        if 'SpectrumIDFormat' not in sp_datum or sp_datum['SpectrumIDFormat'] is None:
            raise MzIdParseException('SpectraData is missing SpectrumIdFormat')
        if not hasattr(sp_datum['SpectrumIDFormat'], 'accession'):
            raise MzIdParseException('SpectraData.SpectrumIdFormat is missing accession')
        if sp_datum['SpectrumIDFormat'].accession is None:
            raise MzIdParseException('SpectraData.SpectrumIdFormat is missing accession')

        # FileFormat
        if 'FileFormat' not in sp_datum or sp_datum['FileFormat'] is None:
            raise MzIdParseException('SpectraData is missing FileFormat')
        if not hasattr(sp_datum['FileFormat'], 'accession'):
            raise MzIdParseException('SpectraData.FileFormat is missing accession')
        if sp_datum['FileFormat'].accession is None:
            raise MzIdParseException('SpectraData.FileFormat is missing accession')

        # location
        if 'location' not in sp_datum or sp_datum['location'] is None:
            raise MzIdParseException('SpectraData is missing location')

    def init_peak_list_readers(self):
        """
        Sets self.peak_list_readers by looping through SpectraData elements

        dictionary:
            key: spectra_data_ref
            value: associated peak_list_reader
        """
        peak_list_readers = {}
        for spectra_data_id in self.mzid_reader._offset_index["SpectraData"].keys():
            sp_datum = self.mzid_reader.get_by_id(spectra_data_id, tag_id='SpectraData')

            self.check_spectra_data_validity(sp_datum)

            sd_id = sp_datum['id']
            peak_list_file_name = ntpath.basename(sp_datum['location'])
            peak_list_file_path = self.peak_list_dir + peak_list_file_name

            try:
                peak_list_reader = PeakListWrapper(
                    peak_list_file_path,
                    sp_datum['FileFormat'].accession,
                    sp_datum['SpectrumIDFormat'].accession
                )
            # ToDo: gz/zip code parts could do with refactoring
            except Exception:
                # try gz version
                try:
                    peak_list_reader = PeakListWrapper(
                        PeakListWrapper.extract_gz(peak_list_file_path + '.gz'),
                        sp_datum['FileFormat'].accession,
                        sp_datum['SpectrumIDFormat'].accession
                    )
                except IOError:
                    raise MzIdParseException('Missing peak list file: %s' % peak_list_file_path)

            peak_list_readers[sd_id] = peak_list_reader

        self.peak_list_readers = peak_list_readers

    def parse_analysis_protocol_collection(self):
        """Parse the AnalysisProtocolCollection and write SpectrumIdentificationProtocols."""
        self.logger.info('parsing AnalysisProtocolCollection- start')
        start_time = time()

        sid_protocols = []
        search_modifications = []
        enzymes = []
        for sid_protocol_id in self.mzid_reader._offset_index[
            'SpectrumIdentificationProtocol'].keys():
            sid_protocol = self.mzid_reader.get_by_id(sid_protocol_id, detailed=True)

            # FragmentTolerance
            try:
                frag_tol = sid_protocol['FragmentTolerance']
                frag_tol_plus = frag_tol['search tolerance plus value']
                frag_tol_value = re.sub('[^0-9,.]', '', str(frag_tol_plus))
                if frag_tol_plus.unit_info.lower() == 'parts per million':
                    frag_tol_unit = 'ppm'
                elif frag_tol_plus.unit_info.lower() == 'dalton':
                    frag_tol_unit = 'Da'
                else:
                    frag_tol_unit = frag_tol_plus.unit_info

                if not all([
                    frag_tol['search tolerance plus value'] ==
                    frag_tol['search tolerance minus value'],
                    frag_tol['search tolerance plus value'].unit_info ==
                    frag_tol['search tolerance minus value'].unit_info
                ]):
                    raise MzIdParseException("Different values for search tolerance plus value"
                                             "and minus value are not yet supported.")

            except KeyError:
                self.warnings.append({
                    "type": "mzidParseError",
                    "message": "could not parse ms2tolerance. Falling back to default: 10 ppm.",
                })
                frag_tol_value = '10'
                frag_tol_unit = 'ppm'

            try:
                analysis_software = json.dumps(self.mzid_reader.get_by_id(
                    sid_protocol['analysisSoftware_ref']))
            except KeyError:
                analysis_software = '{}'

            # Fragmentation ions
            add_sp = sid_protocol.get('AdditionalSearchParams', {})
            # get cvParams that are children of 'ion series considered in search' (MS:1002473)
            ions = self.get_cv_params(add_sp, 'MS:1002473')
            ions = [i.accession for i in ions]

            # fall back to using b and y ions
            if len(ions) == 0:
                ions = ['MS:1001118', 'MS:1001262']
                self.warnings.append(
                    'mzidentML file does not specify any fragment ions (child terms of MS_1002473) '
                    'within <AdditionalSearchParams>. Falling back to b and y ions.')

            data = {
                'id': sid_protocol['id'],
                'upload_id': self.writer.upload_id,
                # ToDo: split into multiple cols
                'frag_tol': f'{frag_tol_value} {frag_tol_unit}',
                'ions': ions,
                'analysis_software': analysis_software
            }

            # Modifications
            mod_index = 0
            for mod in sid_protocol['ModificationParams']['SearchModification']:
                accessions = self.get_accessions(mod)

                # parse specificity rule accessions
                specificity_rules = mod.get('SpecificityRules', [])
                spec_rule_accessions = []
                for spec_rule in specificity_rules:
                    spec_rule_accession = self.get_accessions(spec_rule)
                    if len(spec_rule_accession) != 1:
                        raise MzIdParseException(
                            f'Error when parsing SpecificityRules from SearchModification:\n'
                            f'{json.dumps(mod)}')
                    spec_rule_accessions.append(spec_rule_accession[0])

                # other modifications
                # name
                mod_name = None
                mod_accession = None
                crosslinker_id = None
                # find the matching accession for the name cvParam.
                for i, acc in enumerate(accessions):
                    # ToDo: be more strict with the allowed accessions?
                    match = re.match('(?:MOD|UNIMOD|MS|XLMOD):[0-9]+', acc)
                    if match:
                        # not cross-link donor
                        if match.group() != 'MS:1002509':
                            mod_accession = acc
                        # if cross-link acceptor/donor get the value of the cvParam as crosslink_id
                        if match.group() == 'MS:1002509' or match.group() == 'MS:1002510':
                            crosslinker_id = mod[list(mod)[i]]
                        # name
                        # unknown modification
                        if match.group() == 'MS:1001460':
                            mod_name = "({0:.2f})".format(mod['massDelta'])
                        # others
                        elif match.group() != 'MS:1002509':
                            # name is the key in mod dict corresponding to the matched accession.
                            mod_name = list(mod.keys())[i]

                if mod_name is None or mod_accession is None:
                    raise MzIdParseException(
                        f'Error parsing <SearchModification>s! '
                        f'Could not parse name/accession of modification:\n{json.dumps(mod)}')

                if crosslinker_id:
                    crosslinker_id = str(crosslinker_id)  # it's a string but don't want to convert null to word 'None'

                search_modifications.append({
                    'id': mod_index,
                    'upload_id': self.writer.upload_id,
                    'protocol_id': sid_protocol['id'],
                    'mod_name': mod_name,
                    'mass': mod['massDelta'],
                    'residues': ''.join([r for r in mod['residues'] if r != ' ']),
                    'specificity_rules': spec_rule_accessions,
                    'fixed_mod': mod['fixedMod'],
                    'accession': mod_accession,
                    'crosslinker_id': crosslinker_id
                })
                mod_index += 1

            # Enzymes
            for enzyme in sid_protocol['Enzymes']['Enzyme']:

                enzyme_name = None
                enzyme_accession = None

                # optional child element SiteRegexp
                site_regexp = enzyme.get('SiteRegexp', None)

                # optional child element EnzymeName
                try:
                    enzyme_name_el = enzyme['EnzymeName']
                    # get cvParams that are children of 'cleavage agent name' (MS:1001045)
                    # there is a mandatory UserParam subelement of EnzymeName which we are ignoring
                    enzyme_name = self.get_cv_params(enzyme_name_el, 'MS:1001045')
                    if len(enzyme_name) > 1:
                        raise MzIdParseException(
                            f'Error when parsing EnzymeName from Enzyme:\n{json.dumps(enzyme)}')
                    enzyme_name_cv = list(enzyme_name.keys())[0]
                    enzyme_name = enzyme_name_cv
                    enzyme_accession = enzyme_name_cv.accession
                    # if the site_regexp was missing look it up using obo
                    if site_regexp is None:
                        for child, parent, key in self.ms_obo.out_edges(enzyme_accession,
                                                                        keys=True):
                            if key == 'has_regexp':
                                site_regexp = self.ms_obo.nodes[parent]['name']
                # fallback if no EnzymeName
                except KeyError:
                    try:
                        # optional potentially ambiguous common name
                        enzyme_name = enzyme['name']
                    except KeyError:
                        # no name attribute
                        pass

                enzymes.append({
                    'id': enzyme['id'],
                    'upload_id': self.writer.upload_id,
                    'protocol_id': sid_protocol['id'],
                    'name': enzyme_name,
                    'c_term_gain': enzyme.get('cTermGain', None),
                    'n_term_gain': enzyme.get('nTermGain', None),
                    'min_distance': enzyme.get('minDistance', None),
                    'missed_cleavages': enzyme.get('missedCleavages', None),
                    'semi_specific': enzyme.get('semiSpecific', None),
                    'site_regexp': site_regexp,
                    'accession': enzyme_accession
                })

            sid_protocols.append(data)

        self.mzid_reader.reset()
        self.logger.info('parsing AnalysisProtocolCollection - done. Time: {} sec'.format(
            round(time() - start_time, 2)))

        self.writer.write_data('SpectrumIdentificationProtocol', sid_protocols)
        self.writer.write_data('SearchModification', search_modifications)
        self.writer.write_data('Enzyme', enzymes)
        self.search_modifications = search_modifications

    # def check_all_spectra_data_validity(self):
    #     for spectra_data_id in self.mzid_reader._offset_index["SpectraData"].keys():
    #         sp_datum = self.mzid_reader.get_by_id(spectra_data_id, tag_id='SpectraData')
    #         self.check_spectra_data_validity(sp_datum)
    #

    def parse_db_sequences(self):
        """Parse and write the DBSequences."""
        self.logger.info('parse db sequences - start')
        start_time = time()

        db_sequences = []
        for db_id in self.mzid_reader._offset_index["DBSequence"].keys():
            db_sequence = self.mzid_reader.get_by_id(db_id, tag_id='DBSequence')
            db_sequence_accessions = self.get_accessions(db_sequence)
            db_sequence_data = {
                'id': db_id,
                'accession': db_sequence["accession"],
                'upload_id': self.writer.upload_id
            }

            # name, optional elem att
            if "name" in db_sequence:
                db_sequence_data['name'] = db_sequence["name"]
            else:
                db_sequence_data['name'] = db_sequence["accession"]

            # description
            try:
                # get the key by checking for the protein description accession number
                desc_key = list(db_sequence.keys())[db_sequence_accessions.index('MS:1001088')]
                db_sequence_data['description'] = db_sequence[desc_key]
            except ValueError:
                db_sequence_data['description'] = None

            # Seq is optional child elem of DBSequence
            if "Seq" in db_sequence and isinstance(db_sequence["Seq"], str):
                db_sequence_data['sequence'] = db_sequence["Seq"]
            elif "length" in db_sequence:
                db_sequence_data['sequence'] = "X" * db_sequence["length"]
            else:
                # todo: get sequence
                db_sequence_data['sequence'] = ""

            db_sequences.append(db_sequence_data)

        self.writer.write_data('DBSequence', db_sequences)

        self.logger.info('parse db sequences - done. Time: {} sec'.format(
            round(time() - start_time, 2)))

    def parse_peptides(self):
        """Parse and write the peptides."""
        start_time = time()
        self.logger.info('parse peptides - start')

        search_mod_accessions = [m['accession'] for m in self.search_modifications]

        peptide_index = 0
        peptides = []
        for pep_id in self.mzid_reader._offset_index["Peptide"].keys():
            peptide = self.mzid_reader.get_by_id(pep_id, tag_id='Peptide')

            link_site1 = -1  # ToDo: None?
            crosslinker_modmass = 0
            crosslinker_pair_id = None
            crosslinker_accession = None

            mod_pos = []
            mod_accessions = []
            mod_masses = []
            if 'Modification' in peptide.keys():
                # parse modifications and crosslink info
                for mod in peptide['Modification']:
                    accessions = self.get_accessions(mod)
                    # mod_location is 0-based for assigning modifications to correct amino acid
                    # mod['location'] is 1-based with 0 = n-terminal and len(pep)+1 = C-terminal
                    if mod['location'] == 0:
                        mod_location = 0
                    elif mod['location'] == len(peptide['PeptideSequence']) + 1:
                        mod_location = mod['location'] - 2
                    else:
                        mod_location = mod['location'] - 1

                    # parse crosslinker info
                    # ToDo: crosslinker mod mass should go into Crosslinker Table together with
                    #   specificity info. Mapping to this table would work same as for modifications
                    if 'MS:1002509' in accessions or 'MS:1002510' in accessions:
                        # use mod['location'] for link-site (1-based in database in line with
                        # mzIdentML specifications)
                        link_site1 = mod['location']
                        # cross-link donor
                        if 'MS:1002509' in accessions:
                            key = list(mod.keys())[accessions.index('MS:1002509')]
                            crosslinker_pair_id = mod[key]
                            crosslinker_modmass = mod['monoisotopicMassDelta']
                            crosslinker_accession = mod['name'].accession
                        # cross-link acceptor/receiver
                        if 'MS:1002510' in accessions:
                            key = list(mod.keys())[accessions.index('MS:1002510')]
                            crosslinker_pair_id = mod[key]
                            crosslinker_modmass = mod['monoisotopicMassDelta'] # should be zero but i guess include anyway? - CC

                    else:  # save the modification info if it's not crosslink related
                        # Commented out block that tried to match modifications on peptides to SearchModifications
                        # ToDo: Might want to revisit this in the future
                        # if mod['name'].accession == 'MS:1001460':  # unknown modification
                        #     # loop over search modifications and try to match by mass and residues
                        #     m_ids = []
                        #     # monoisotopicMassDelta is optional ToDo: what if not present?
                        #     mod_mass = mod.get('monoisotopicMassDelta', None)
                        #     # residues is optional, so fall back to getting the modified amino acid
                        #     mod_residues = mod.get('residues',
                        #                            [peptide['PeptideSequence'][mod_location]])
                        #     for i, sm in enumerate(self.search_modifications):
                        #         # this doesn't seem super reliable coz of rounding errors in mod masses - cc
                        #         if sm['accession'] == 'MS:1001460' and sm['mass'] == mod_mass and \
                        #                 all([m in sm['residues'] for m in mod_residues]):
                        #             m_ids.append(i)
                        #     if len(m_ids) != 1:
                        #         raise MzIdParseException(
                        #             f'Could not map unknown modification to <SearchModifications>:'
                        #             f'\n{json.dumps(mod)}')
                        #     else:
                        #         mod_ids.append(m_ids[0])
                        # else:  # not unknown modification accession
                        #     try:
                        #         mod_ids.append(search_mod_accessions.index(mod['name'].accession))
                        #     except ValueError:
                        #         MzIdParseException(
                        #             f'Modification not found in <SearchModification>s: '
                        #             f'{json.dumps(mod)}')

                        mod_pos.append(mod_location)
                        mod_accessions.append(mod['name'].accession)
                        mod_masses.append(mod.get('monoisotopicMassDelta', None))

            peptide_data = {
                'id': peptide['id'],
                'upload_id': self.writer.upload_id,
                'base_sequence': peptide['PeptideSequence'],
                'modification_accessions': mod_accessions,
                'modification_positions': mod_pos,
                'modification_masses': mod_masses,
                'link_site1': link_site1,
                # 'link_site2': link_site2,  # ToDo: loop link support
                'crosslinker_modmass': crosslinker_modmass,
                'crosslinker_pair_id': str(crosslinker_pair_id),
                'crosslinker_accession': crosslinker_accession
            }

            peptides.append(peptide_data)

            # Batch write 1000 peptides into the DB
            if peptide_index % 1000 == 0:
                self.logger.info('writing 1000 peptides to DB')
                try:
                    self.writer.write_data('ModifiedPeptide', peptides)
                    peptides = []
                except Exception as e:
                    raise e
            peptide_index += 1

        # write the remaining peptides
        try:
            self.writer.write_data('ModifiedPeptide', peptides)
        except Exception as e:
            raise e

        self.logger.info(
            f'parse peptides - done. Time: {round(time() - start_time, 2)} sec')

    def parse_peptide_evidences(self):
        """Parse and write the peptide evidences."""
        start_time = time()
        self.logger.info('parse peptide evidences - start')

        pep_evidences = []
        for pep_ev_id in self.mzid_reader._offset_index["PeptideEvidence"].keys():
            peptide_evidence = self.mzid_reader.get_by_id(pep_ev_id, tag_id='PeptideEvidence',
                                                          retrieve_refs=False)

            pep_start = -1
            if "start" in peptide_evidence:
                pep_start = peptide_evidence["start"]  # start att, optional

            is_decoy = False
            if "isDecoy" in peptide_evidence:
                is_decoy = peptide_evidence["isDecoy"]  # isDecoy att, optional

            pep_ev_data = {
                'upload_id': self.writer.upload_id,
                'peptide_ref': peptide_evidence["peptide_ref"],
                'dbsequence_ref': peptide_evidence["dBSequence_ref"],
                # 'protein_accession': seq_id_to_acc_map[peptide_evidence["dBSequence_ref"]],
                'pep_start': pep_start,
                'is_decoy': is_decoy,
            }

            pep_evidences.append(pep_ev_data)

            # Batch write 1000 peptide evidences into the DB
            if len(pep_evidences) % 1000 == 0:
                self.logger.info('writing 1000 peptide_evidences to DB')
                try:
                    self.writer.write_data('PeptideEvidence', pep_evidences)
                    pep_evidences = []
                except Exception as e:
                    raise e

        # write the remaining data
        try:
            self.writer.write_data('PeptideEvidence', pep_evidences)
        except Exception as e:
            raise e

        self.mzid_reader.reset()

        self.logger.info('parse peptide evidences - done. Time: {} sec'.format(
            round(time() - start_time, 2)))

    def main_loop(self):
        """Parse the <SpectrumIdentificationResult>s and <SpectrumIdentificationItem>s within."""
        main_loop_start_time = time()
        self.logger.info('main loop - start')

        spec_count = 0
        spectra = []
        spectrum_identifications = []
        for sid_result in self.mzid_reader:
            if self.peak_list_dir:
                peak_list_reader = self.peak_list_readers[sid_result['spectraData_ref']]

                spectrum = peak_list_reader[sid_result["spectrumID"]]
                spectra.append({
                    'id': sid_result["spectrumID"],
                    'spectra_data_ref': sid_result['spectraData_ref'],
                    'upload_id': self.writer.upload_id,
                    'scan_id': spectrum.scan_id,  # ToDo: Do we need this parsed scan_id?
                    # ToDo: from Spectrum?
                    'peak_list_file_name': ntpath.basename(peak_list_reader.peak_list_path),
                    'precursor_mz': spectrum.precursor['mz'],
                    'precursor_charge': spectrum.precursor['charge'],
                    'mz': spectrum.mz_values,
                    'intensity': spectrum.int_values,
                })

            spectrum_ident_dict = dict()

            for spec_id_item in sid_result['SpectrumIdentificationItem']:
                # get suitable id # ToDo: use accession instead of cvParam string?
                if 'cross-link spectrum identification item' in spec_id_item.keys():
                    self.contains_crosslinks = True
                    crosslink_id = spec_id_item['cross-link spectrum identification item']
                else:  # assuming linear
                    crosslink_id = None

                # check if seen it before
                if crosslink_id in spectrum_ident_dict.keys():
                    # do crosslink specific stuff
                    ident_data = spectrum_ident_dict.get(crosslink_id)
                    ident_data['pep2_id'] = spec_id_item['peptide_ref']
                else:
                    # do stuff common to linears and crosslinks
                    # ToDo: refactor with MS: cvParam list of all scores
                    scores = {
                        k: v for k, v in spec_id_item.items()
                        if 'score' in k.lower() or
                           'pvalue' in k.lower() or
                           'evalue' in k.lower() or
                           'sequest' in k.lower() or
                           'scaffold' in k.lower()
                    }

                    rank = spec_id_item['rank']
                    # from mzidentML schema 1.2.0: For PMF data, the rank attribute may be
                    # meaningless and values of rank = 0 should be given.
                    # xiSPEC front-end expects rank = 1 as default
                    if rank is None or int(rank) == 0:
                        rank = 1

                    calculated_mass_to_charge = None
                    if 'calculatedMassToCharge' in spec_id_item.keys():
                        calculated_mass_to_charge = float(spec_id_item['calculatedMassToCharge'])

                    ident_data = {
                        'id': spec_id_item['id'],
                        'upload_id': self.writer.upload_id,
                        'spectrum_id': sid_result['spectrumID'],
                        'spectra_data_ref': sid_result['spectraData_ref'],
                        'pep1_id': spec_id_item['peptide_ref'],
                        'pep2_id': None,
                        'charge_state': int(spec_id_item['chargeState']),
                        'pass_threshold': spec_id_item['passThreshold'],
                        'rank': int(rank),
                        'scores': scores,
                        'exp_mz': spec_id_item['experimentalMassToCharge'],
                        'calc_mz': calculated_mass_to_charge,
                    }

                    if crosslink_id:
                        spectrum_ident_dict[crosslink_id] = ident_data

            spectrum_identifications += spectrum_ident_dict.values()
            spec_count += 1

            if spec_count % 1000 == 0:
                self.logger.info('writing 1000 entries (1000 spectra and their idents) to DB')
                try:
                    if self.peak_list_dir:
                        self.writer.write_data('Spectrum', spectra)
                    spectra = []
                    self.writer.write_data('SpectrumIdentification', spectrum_identifications)
                    spectrum_identifications = []
                except Exception as e:
                    raise e

        # end main loop
        self.logger.info('main loop - done Time: {} sec'.format(
            round(time() - main_loop_start_time, 2)))

        # once loop is done write remaining data to DB
        db_wrap_up_start_time = time()
        self.logger.info('write remaining entries to DB - start')

        if self.peak_list_dir:
            self.writer.write_data('Spectrum', spectra)
        self.writer.write_data('SpectrumIdentification', spectrum_identifications)

        self.logger.info('write remaining entries to DB - done.  Time: {} sec'.format(
            round(time() - db_wrap_up_start_time, 2)))

    def upload_info(self):
        upload_info_start_time = time()
        self.logger.info('parse upload info - start')

        spectra_formats = []
        for spectra_data_id in self.mzid_reader._offset_index["SpectraData"].keys():
            sp_datum = self.mzid_reader.get_by_id(spectra_data_id, tag_id='SpectraData',
                                                  detailed=True)
            spectra_formats.append(sp_datum)
        spectra_formats = json.dumps(spectra_formats, cls=NumpyEncoder)

        # Provider - optional element
        try:
            provider = json.dumps(self.mzid_reader.iterfind('Provider').next())
        except StopIteration:
            provider = '{}'
        except Exception as e:
            raise MzIdParseException(type(e).__name__, e.args)
        self.mzid_reader.reset()

        # AuditCollection - optional element
        try:
            audits = json.dumps(self.mzid_reader.iterfind('AuditCollection').next())
        except StopIteration:
            audits = '{}'
        except Exception as e:
            raise MzIdParseException(type(e).__name__, e.args)
        self.mzid_reader.reset()

        # AnalysisSampleCollection - optional element
        try:
            samples = json.dumps(
                self.mzid_reader.iterfind('AnalysisSampleCollection').next()['Sample'])
        except StopIteration:
            samples = '{}'
        except Exception as e:
            raise MzIdParseException(type(e).__name__, e.args)
        self.mzid_reader.reset()

        # BibliographicReference - optional element
        bib_refs = []
        for bib in self.mzid_reader.iterfind('BibliographicReference'):
            bib_refs.append(bib)
        bib_refs = json.dumps(bib_refs)
        self.mzid_reader.reset()

        self.writer.write_mzid_info(spectra_formats, provider, audits, samples, bib_refs)

        self.logger.info('getting upload info - done  Time: {} sec'.format(
            round(time() - upload_info_start_time, 2)))

    def fill_in_missing_scores(self):
        pass

    def write_new_upload(self):
        """Write new upload."""
        upload_data = {
                'id': self.writer.upload_id,
                'user_id': self.writer.user_id,
                'identification_file_name': os.path.basename(self.mzid_path),
        }
        self.writer.write_data('Upload', upload_data)

    def write_other_info(self):
        """Write remaining information into Upload table."""
        self.writer.write_other_info(self.contains_crosslinks, self.warnings)

    @staticmethod
    def get_accessions(element):
        """Get the cvParam accessions for the given element."""
        accessions = []
        for el in element.keys():
            if hasattr(el, 'accession'):
                accessions.append(el.accession)
            else:
                accessions.append('')
        return accessions

    def get_cv_params(self, element, super_cls_accession=None):
        """
        Get the cvParams of an element.

        :param element: (dict) element from MzIdParser (pyteomics).
        :param super_cls_accession: (str) accession number of the superclass
        :return: filtered dictionary of cvParams
        """
        accessions = self.get_accessions(element)

        if super_cls_accession is None:
            filtered_idx = [i for i, a in enumerate(accessions) if a != '']
        else:
            children = []
            if type(super_cls_accession) != list:
                super_cls_accession = [super_cls_accession]
            for sp_accession in super_cls_accession:

                for child, parent, key in self.ms_obo.in_edges(sp_accession, keys=True):
                    if key != 'is_a':
                        continue
                    children.append(child)
            filtered_idx = [i for i, a in enumerate(accessions) if a in children]

        return {k: v for i, (k, v) in enumerate(element.items()) if i in filtered_idx}

    # ToDo: refactor gz/zip
    # split into two functions
    @staticmethod
    def extract_mzid(archive):
        if archive.endswith('zip'):
            zip_ref = zipfile.ZipFile(archive, 'r')
            unzip_path = archive + '_unzip/'
            zip_ref.extractall(unzip_path)
            zip_ref.close()

            return_file_list = []

            for root, dir_names, file_names in os.walk(unzip_path):
                file_names = [f for f in file_names if not f[0] == '.']
                dir_names[:] = [d for d in dir_names if not d[0] == '.']
                for file_name in file_names:
                    os.path.join(root, file_name)
                    if file_name.lower().endswith('.mzid'):
                        return_file_list.append(root + '/' + file_name)
                    else:
                        raise IOError('unsupported file type: %s' % file_name)

            if len(return_file_list) > 1:
                raise BaseException("more than one mzid file found!")

            return return_file_list[0]

        elif archive.endswith('gz'):
            in_f = gzip.open(archive, 'rb')
            archive = archive.replace(".gz", "")
            out_f = open(archive, 'wb')
            try:
                out_f.write(in_f.read())
            except IOError:
                raise BaseException('Zip archive error: %s' % archive)

            in_f.close()
            out_f.close()

            return archive

        else:
            raise BaseException('unsupported file type: %s' % archive)


class xiSPEC_MzIdParser(MzIdParser):

    def write_new_upload(self):
        """Overrides base class function - not needed for xiSPEC."""
        pass

    def upload_info(self):
        """Overrides base class function - not needed for xiSPEC."""
        pass

    def parse_db_sequences(self):
        """Overrides base class function - not needed for xiSPEC."""
        pass

    def fill_in_missing_scores(self):
        # Fill missing scores with
        score_fill_start_time = time()
        self.logger.info('fill in missing scores - start')
        self.writer.fill_in_missing_scores()
        self.logger.info('fill in missing scores - done. Time: {}'.format(
            round(time() - score_fill_start_time, 2)))

    def write_other_info(self):
        """Overrides base class function - not needed for xiSPEC."""
        pass
