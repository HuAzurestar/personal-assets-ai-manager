// Standalone contracts consume backend metadata, not a copied currency table.
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function unitFixture() {
  const program = `
import os, sys, tempfile
with tempfile.TemporaryDirectory(prefix='paam-unit-contract-') as directory:
    for key in list(os.environ):
        if key.startswith('PAAM_'):
            del os.environ[key]
    os.environ['PAAM_DATA_DIR'] = directory
    os.environ['PAAM_SQL_WEB_ENABLED'] = '0'
    os.environ['PAAM_AUTOTAG_REAL_ANALYSIS'] = '0'
    sys.path.insert(0, 'src')
    from backend.service.unit_dictionary_service import UnitDictionaryService
    print(UnitDictionaryService().get().model_dump_json())
`;
  return JSON.parse(execFileSync(process.env.PAAM_TEST_PYTHON || 'python', ['-c', program], {
    cwd: path.resolve(__dirname, '../..'), encoding: 'utf8', timeout: 15000,
    env: {...process.env, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1'},
  })).items;
}

function installFixture(context) {
  context.TextEncoder = TextEncoder;
  context.unitFixture = unitFixture();
  const source = fs.readFileSync(path.resolve(__dirname, '../frontend/js/util/unit-dictionary.js'), 'utf8')
    .replace(/^export /gm, '');
  vm.runInContext(source, context);
  vm.runInContext('installUnitDictionary(unitFixture)', context);
}

module.exports = {unitFixture, installFixture};
