require("@nomicfoundation/hardhat-toolbox");

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
    },
  },
  defaultNetwork: "geth",
  networks: {
    hardhat: {},
    geth: {
      url: "http://127.0.0.1:8545",
      chainId: 1337,
      // In geth --dev mode, accounts are managed by the node.
      // Leave this empty and use ethers provider signer from RPC account.
      accounts: [],
    },
  },
};
