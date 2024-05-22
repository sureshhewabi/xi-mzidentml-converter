from sqlalchemy import Table
import os
import logging
from .db_pytest_fixtures import *
from .parse_mzid import parse_mzid_into_postgresql

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger(__name__)


def test_psql_multi_spectra_mzid_parser(tmpdir, db_info, use_database, engine):
    # file paths
    fixtures_dir = os.path.join(os.path.dirname(__file__), 'fixtures', 'mzid_parser', '1.3.0')
    mzid = os.path.join(fixtures_dir, 'multiple_spectra_per_id_1_3_0_draft.mzid')
    peak_list_folder = False

    id_parser = parse_mzid_into_postgresql(mzid, peak_list_folder, tmpdir, logger, use_database,
                                           engine)

    with engine.connect() as conn:
        # Match
        stmt = Table("match", id_parser.writer.meta, autoload_with=id_parser.writer.engine,
                     quote=False).select()
        rs = conn.execute(stmt)
        results = rs.fetchall()
        assert len(results) == 6

        assert results[0].id == 'HCD_SII_0'
        assert results[0].pep1_id == 'p1'
        assert results[0].pep2_id == 'p2'
        assert results[0].multiple_spectra_identification_id == 1234
        assert results[0].multiple_spectra_identification_pc == 'P'
        assert results[0].sil_id == 'sil_HCD'

        assert results[1].id == 'ETD_SII_0'
        assert results[1].pep1_id == 'p1'
        assert results[1].pep2_id == 'p2'
        assert results[1].multiple_spectra_identification_id == 1234
        assert results[1].multiple_spectra_identification_pc == 'P'
        assert results[1].sil_id == 'sil_ETD'

        assert results[2].id == 'MS3_SII_0'
        assert results[2].pep1_id == 'p1_a'
        assert results[2].pep2_id == None
        assert results[2].multiple_spectra_identification_id == 1234
        assert results[2].multiple_spectra_identification_pc == 'C'
        assert results[2].sil_id == 'sil_MS3'

        assert results[3].id == 'MS3_SII_1'
        assert results[3].pep1_id == 'p2_t'
        assert results[3].pep2_id == None
        assert results[3].multiple_spectra_identification_id == 1234
        assert results[3].multiple_spectra_identification_pc == 'C'
        assert results[3].sil_id == 'sil_MS3'

        assert results[4].id == 'MS3_SII_2'
        assert results[4].pep1_id == 'p1_t'
        assert results[4].pep2_id == None
        assert results[4].multiple_spectra_identification_id == 1234
        assert results[4].multiple_spectra_identification_pc == 'C'
        assert results[4].sil_id == 'sil_MS3'

        assert results[5].id == 'MS3_SII_3'
        assert results[5].pep1_id == 'p2_a'
        assert results[5].pep2_id == None
        assert results[5].multiple_spectra_identification_id == 1234
        assert results[5].multiple_spectra_identification_pc == 'C'
        assert results[5].sil_id == 'sil_MS3'

    engine.dispose()


def test_psql_looplink_mzid_parser(tmpdir, db_info, use_database, engine):
    # file paths
    fixtures_dir = os.path.join(os.path.dirname(__file__), 'fixtures', 'mzid_parser', '1.3.0')
    mzid = os.path.join(fixtures_dir, 'Xlink_EDC_mzIdentML_1_3_0_draft.mzid')
    peak_list_folder = False

    id_parser = parse_mzid_into_postgresql(mzid, peak_list_folder, tmpdir, logger, use_database,
                                           engine)

    with engine.connect() as conn:
        # Match
        t = Table("match", id_parser.writer.meta, autoload_with=id_parser.writer.engine,
                  quote=False)
        stmt = t.select().where(t.c.id == 'SII_7_1')
        rs = conn.execute(stmt)
        results = rs.fetchall()
        assert len(results) == 1

        assert results[0].id == 'SII_7_1'
        assert results[0].pep1_id == 'peptide_7_1'
        assert results[0].pep2_id == None

        t = Table("modifiedpeptide", id_parser.writer.meta, autoload_with=id_parser.writer.engine,
                  quote=False)
        stmt = t.select().where(t.c.id == 'peptide_7_1')

        rs = conn.execute(stmt)
        results = rs.fetchall()
        assert len(results) == 1

        assert results[0].id == 'peptide_7_1'
        assert results[0].base_sequence == 'DVIQSLVDDDLVAK'
        assert results[0].mod_accessions == []
        assert results[0].mod_avg_mass_deltas == []
        assert results[0].mod_monoiso_mass_deltas == []
        assert results[0].mod_positions == []
        assert results[0].link_site1 == 10
        assert results[0].link_site2 == 14
        assert results[0].crosslinker_modmass == -18.010565
        assert results[0].crosslinker_pair_id == '100.0'
        assert results[0].crosslinker_accession == 'UNIMOD:2018'

    engine.dispose()


def test_psql_looplink_mzid_parser(tmpdir, db_info, use_database, engine):
    # file paths
    fixtures_dir = os.path.join(os.path.dirname(__file__), 'fixtures', 'mzid_parser', '1.3.0')
    mzid = os.path.join(fixtures_dir, 'noncovalently_assoc_1_3_0_draft.mzid')
    peak_list_folder = False

    id_parser = parse_mzid_into_postgresql(mzid, peak_list_folder, tmpdir, logger, use_database,
                                           engine)

    with engine.connect() as conn:
        # Match
        t = Table("match", id_parser.writer.meta, autoload_with=id_parser.writer.engine,
                  quote=False)
        stmt = t.select().where(t.c.id == 'SII_1_1')
        rs = conn.execute(stmt)
        results = rs.fetchall()
        assert len(results) == 1

        assert results[0].id == 'SII_1_1'
        assert results[0].pep1_id == 'p1'
        assert results[0].pep2_id == 'p2'

        t = Table("modifiedpeptide", id_parser.writer.meta, autoload_with=id_parser.writer.engine,
                  quote=False)
        stmt = t.select().where(t.c.id == 'p1')

        rs = conn.execute(stmt)
        results = rs.fetchall()
        assert len(results) == 1

        assert results[0].id == 'p1'
        assert results[0].base_sequence == 'AYALMTDIHWDDCFCR'
        assert results[0].mod_accessions == [{'MS:1003393': 'ox', 'UNIMOD:35': 'Oxidation'},
                                             {'MS:1003393': 'cm', 'UNIMOD:4': 'Carbamidomethyl'},
                                             {'MS:1003393': 'cm', 'UNIMOD:4': 'Carbamidomethyl'}]
        assert results[0].mod_avg_mass_deltas == [None, None, None]
        assert results[0].mod_monoiso_mass_deltas == [15.99491, 57.02147, 57.02147]
        assert results[0].mod_positions == [5, 13, 15]
        assert results[0].link_site1 == None
        assert results[0].link_site2 == None
        assert results[0].crosslinker_modmass == 0.0
        assert results[0].crosslinker_pair_id == None
        assert results[0].crosslinker_accession == None

        t = Table("modifiedpeptide", id_parser.writer.meta, autoload_with=id_parser.writer.engine,
                  quote=False)
        stmt = t.select().where(t.c.id == 'p2')

        rs = conn.execute(stmt)
        results = rs.fetchall()
        assert len(results) == 1

        assert results[0].id == 'p2'
        assert results[0].base_sequence == 'VHTECCHGDLLECADDR'
        assert results[0].mod_accessions == [{'MS:1003393': 'cm', 'UNIMOD:4': 'Carbamidomethyl'},
                                             {'MS:1003393': 'cm', 'UNIMOD:4': 'Carbamidomethyl'},
                                             {'MS:1003393': 'cm', 'UNIMOD:4': 'Carbamidomethyl'}]
        assert results[0].mod_avg_mass_deltas == [None, None, None]
        assert results[0].mod_monoiso_mass_deltas == [57.02147, 57.02147, 57.02147]
        assert results[0].mod_positions == [5, 6, 13]
        assert results[0].link_site1 == None
        assert results[0].link_site2 == None
        assert results[0].crosslinker_modmass == 0.0
        assert results[0].crosslinker_pair_id == None
        assert results[0].crosslinker_accession == None

    engine.dispose()
